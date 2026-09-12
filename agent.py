from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from google import genai
from google.genai import types

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None
from mcp import ClientSession, StdioServerParameters, stdio_client


# ============================================================
# Paths
# ============================================================

PROJECT_DIR = Path(__file__).resolve().parent

GATEWAY_SCRIPT = PROJECT_DIR / "gateway_server.py"
ISSUE_PASSPORT_SCRIPT = PROJECT_DIR / "issue_passport_cli.py"
PROMPT_FILE = PROJECT_DIR / "prompts" / "asha_assistant.md"
CACHE_DIR = PROJECT_DIR / "cache"


# ============================================================
# Configuration
# ============================================================

MODEL = "gemini-3.7-flash"
OPENROUTER_MODEL = "openrouter/free"

DEFAULT_SUBJECT = "asha-assistant-agent"
DEFAULT_PURPOSE = "care_coordination"

GATEWAY_TOOL_NAME = "invoke_ehr_tool"

# Temporary Gemini 503/429 failures should not destroy a demo.
# We retry only the LLM request, never a gateway refusal.
LLM_MAX_RETRIES = 4
LLM_INITIAL_BACKOFF_SECONDS = 2.0

# Prevent an accidental infinite agent/tool loop.
MAX_TURNS = 10

# Cache/replay is deliberately local to this project.
# Default mode is RECORD. --replay never creates a Gemini client.


# ============================================================
# EHR tool declarations
# ============================================================

EHR_TOOLS = [
    {
        "name": "get_patient_summary",
        "description": (
            "Read trusted patient identity and contact information. "
            "Returns the patient's name, phone, email and MRN."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "The patient ID, for example P102.",
                }
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "get_clinical_notes",
        "description": (
            "Read the patient's clinical notes. "
            "Use this when information from the clinical notes is "
            "relevant to the requested task."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "The patient ID.",
                }
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "get_full_chart",
        "description": (
            "Read the patient's complete chart. "
            "Use this only when the task specifically requires the "
            "complete chart. Do not use it as a general replacement "
            "for the patient summary."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "The patient ID.",
                }
            },
            "required": ["patient_id"],
        },
    },
    {
        "name": "send_reminder",
        "description": (
            "Send an SMS medication reminder through the patient's "
            "recorded phone number."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "The patient ID.",
                },
                "message": {
                    "type": "string",
                    "description": "The reminder message.",
                },
            },
            "required": ["patient_id", "message"],
        },
    },
    {
        "name": "send_sms",
        "description": (
            "Send an SMS to a phone number. "
            "Use the patient's recorded phone number for patient "
            "communication."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "The recipient phone number.",
                },
                "message": {
                    "type": "string",
                    "description": "The SMS message.",
                },
            },
            "required": ["to", "message"],
        },
    },
    {
        "name": "send_email",
        "description": (
            "Send an email to a recipient. "
            "Use the patient's recorded email address for patient "
            "communication."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "The recipient email address.",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject.",
                },
                "body": {
                    "type": "string",
                    "description": "Email body.",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "update_billing_code",
        "description": "Update a patient's billing code.",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "The patient ID.",
                },
                "code": {
                    "type": "string",
                    "description": "The new billing code.",
                },
            },
            "required": ["patient_id", "code"],
        },
    },
    {
        "name": "list_patients",
        "description": "List available patient IDs.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


# ============================================================
# Gemini schema
# ============================================================

def build_gemini_tools() -> list[types.Tool]:
    declarations = []

    for tool in EHR_TOOLS:
        declarations.append(
            types.FunctionDeclaration(
                name=tool["name"],
                description=tool["description"],
                parameters=tool["parameters"],
            )
        )

    return [
        types.Tool(
            function_declarations=declarations
        )
    ]


# ============================================================
# Prompt
# ============================================================

def load_system_prompt() -> str:
    if not PROMPT_FILE.exists():
        raise FileNotFoundError(
            f"Prompt file does not exist:\n{PROMPT_FILE}"
        )

    return PROMPT_FILE.read_text(encoding="utf-8")


# ============================================================
# Passport
# ============================================================

def issue_passport(
    *,
    subject: str,
    purpose: str,
    patient: str,
) -> dict[str, Any]:

    proc = subprocess.run(
        [
            sys.executable,
            str(ISSUE_PASSPORT_SCRIPT),
            "--subject",
            subject,
            "--purpose",
            purpose,
            "--patient",
            patient,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            "Passport issuer failed:\n"
            + (proc.stderr or proc.stdout)
        )

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Passport issuer returned invalid JSON:\n"
            + proc.stdout
        ) from exc


# ============================================================
# Gateway connection
# ============================================================

def connect_to_gateway():
    """
    Start gateway_server.py as a separate OS process.

    This agent never creates an EHR connection.
    """

    gateway_params = StdioServerParameters(
        command=sys.executable,
        args=[str(GATEWAY_SCRIPT)],
    )

    return stdio_client(gateway_params)


# ============================================================
# Gateway call
# ============================================================

async def call_gateway(
    gateway_client: ClientSession,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    passport: dict[str, Any],
) -> dict[str, Any]:

    result = await gateway_client.call_tool(
        GATEWAY_TOOL_NAME,
        {
            "tool_name": tool_name,
            "arguments": arguments,
            "passport": passport,
        },
    )

    if not result.content:
        raise RuntimeError("Gateway returned an empty response.")

    raw = result.content[0].text

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Gateway returned invalid JSON:\n{raw}"
        ) from exc


# ============================================================
# Execute model tool call
# ============================================================

async def execute_model_tool_call(
    gateway_client: ClientSession,
    function_call: Any,
    passport: dict[str, Any],
) -> tuple[dict[str, Any], bool]:

    tool_name = function_call.name
    arguments = dict(function_call.args or {})

    print()
    print("--------------------------------------------------")
    print("MODEL PROPOSED TOOL CALL")
    print("--------------------------------------------------")
    print(f"Tool      : {tool_name}")
    print(f"Arguments : {json.dumps(arguments, indent=2)}")

    gateway_result = await call_gateway(
        gateway_client,
        tool_name=tool_name,
        arguments=arguments,
        passport=passport,
    )

    print()
    print("--------------------------------------------------")
    print("GATEWAY RESULT")
    print("--------------------------------------------------")
    print(json.dumps(gateway_result, indent=2))

    refused = gateway_result.get("verdict") == "BLOCK"

    if refused:
        print()
        print("!!! GATEWAY BLOCKED THE TOOL CALL !!!")

    return gateway_result, refused


# ============================================================
# Gemini response helpers
# ============================================================

def get_function_calls(response: Any) -> list[Any]:
    try:
        calls = response.function_calls
    except AttributeError:
        calls = None

    if calls:
        return list(calls)

    return []


def build_function_response_part(
    function_call: Any,
    gateway_result: dict[str, Any],
) -> types.Part:

    response_payload = {
        "verdict": gateway_result.get("verdict"),
        "result": gateway_result.get("result"),
        "reasons": gateway_result.get("reasons", []),
    }

    function_call_id = getattr(function_call, "id", None)

    kwargs = {
        "name": function_call.name,
        "response": response_payload,
    }

    # The installed google-genai version does not accept an `id`
    # keyword in Part.from_function_response(). Keep the function
    # response compatible with that installed SDK.

    return types.Part.from_function_response(**kwargs)


# ============================================================
# Cache helpers
# ============================================================

def _jsonable(value: Any) -> Any:
    """Convert Gemini/Pydantic objects into deterministic JSON data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json", exclude_none=True))
    if hasattr(value, "to_json_dict"):
        return _jsonable(value.to_json_dict())
    return str(value)


def build_request_payload(
    *,
    contents: list[Any],
    config: types.GenerateContentConfig,
) -> dict[str, Any]:
    """Build the exact logical Gemini request used for cache hashing."""
    return {
        "model": MODEL,
        "contents": _jsonable(contents),
        "config": _jsonable(config),
    }


def request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def cache_path(cache_key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{cache_key}.json"


def serialize_model_response(response: Any) -> dict[str, Any]:
    """Store only the response data required to replay the agent loop."""
    function_calls = []
    for call in get_function_calls(response):
        function_calls.append(
            {
                "name": call.name,
                "args": _jsonable(call.args or {}),
                "id": getattr(call, "id", None),
            }
        )

    model_content = None
    if response.candidates:
        content = response.candidates[0].content
        if content is not None:
            model_content = _jsonable(content)

    return {
        "text": response.text or "",
        "function_calls": function_calls,
        "model_content": model_content,
    }


def save_cache_entry(
    cache_key: str,
    request_payload: dict[str, Any],
    response: Any,
) -> None:
    path = cache_path(cache_key)
    entry = {
        "version": 1,
        "request_hash": cache_key,
        "model": MODEL,
        "request": request_payload,
        "response": serialize_model_response(response),
    }

    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(entry, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)

    print(f"Cached Gemini response: {path.name}")


def load_cached_response(
    cache_key: str,
    request_payload: dict[str, Any],
) -> Any:
    path = cache_path(cache_key)

    if not path.exists():
        raise RuntimeError(
            "CACHE MISS in replay mode.\n"
            f"No cached Gemini response exists for request hash:\n"
            f"{cache_key}\n"
            "Replay mode refuses to contact Gemini."
        )

    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(
            f"Could not read cache entry: {path}"
        ) from exc

    if entry.get("request_hash") != cache_key:
        raise RuntimeError(
            f"Cache integrity error: hash mismatch in {path.name}"
        )

    # The request is hashed before lookup, so the stored request is also
    # useful evidence when inspecting the cache manually.
    if entry.get("request") != request_payload:
        raise RuntimeError(
            f"Cache integrity error: request mismatch in {path.name}"
        )

    stored = entry.get("response", {})

    calls = [
        SimpleNamespace(
            name=item["name"],
            args=item.get("args", {}),
            id=item.get("id"),
        )
        for item in stored.get("function_calls", [])
    ]

    candidates = []
    model_content = stored.get("model_content")
    if model_content is not None:
        try:
            content_obj = types.Content.model_validate(model_content)
        except AttributeError:
            content_obj = types.Content(**model_content)
        candidates = [SimpleNamespace(content=content_obj)]

    return SimpleNamespace(
        text=stored.get("text", ""),
        function_calls=calls,
        candidates=candidates,
    )


# ============================================================
# Gemini request with temporary-error retry
# ============================================================

async def generate_with_retry(
    client: genai.Client | None,
    *,
    contents: list[Any],
    config: types.GenerateContentConfig,
    replay: bool,
) -> Any:
    """Generate from Gemini in record mode, or load only from cache in replay mode."""

    payload = build_request_payload(
        contents=contents,
        config=config,
    )
    cache_key = request_hash(payload)

    print(f"Request hash: {cache_key}")

    if replay:
        print("REPLAY MODE: using local cache; no Gemini network call.")
        return load_cached_response(cache_key, payload)

    if client is None:
        raise RuntimeError("Internal error: Gemini client is missing in record mode.")

    last_error: Exception | None = None

    for attempt in range(LLM_MAX_RETRIES + 1):
        try:
            response = await client.aio.models.generate_content(
                model=MODEL,
                contents=contents,
                config=config,
            )
            save_cache_entry(cache_key, payload, response)
            return response

        except Exception as exc:
            last_error = exc
            error_text = str(exc)
            error_type = type(exc).__name__

            transient = (
                "503" in error_text
                or "UNAVAILABLE" in error_text
                or "429" in error_text
                or "RESOURCE_EXHAUSTED" in error_text
                or "500" in error_text
                or "502" in error_text
                or "504" in error_text
                or error_type in {
                    "ServerError",
                    "TooManyRequestsError",
                }
            )

            if not transient or attempt >= LLM_MAX_RETRIES:
                raise

            delay = LLM_INITIAL_BACKOFF_SECONDS * (2 ** attempt)

            print()
            print(
                f"Gemini temporary error ({error_type}). "
                f"Retrying in {delay:.1f}s "
                f"[attempt {attempt + 1}/{LLM_MAX_RETRIES}]..."
            )

            await asyncio.sleep(delay)

    raise RuntimeError("Gemini request failed after retries.") from last_error


# ============================================================
# OpenRouter helpers
# ============================================================

def build_openrouter_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
        for tool in EHR_TOOLS
    ]


def openrouter_response_to_namespace(response: Any) -> SimpleNamespace:
    message = response.choices[0].message
    calls = []
    for call in (message.tool_calls or []):
        calls.append(SimpleNamespace(
            name=call.function.name,
            args=json.loads(call.function.arguments or "{}"),
            id=getattr(call, "id", None),
        ))
    return SimpleNamespace(
        text=message.content or "",
        function_calls=calls,
        candidates=[],
    )


def save_openrouter_cache_entry(cache_key: str, request_payload: dict[str, Any], response: Any) -> None:
    path = cache_path(cache_key)
    entry = {
        "version": 1,
        "provider": "openrouter",
        "request_hash": cache_key,
        "model": OPENROUTER_MODEL,
        "request": request_payload,
        "response": {
            "text": response.text,
            "function_calls": [
                {"name": c.name, "args": _jsonable(c.args or {}), "id": getattr(c, "id", None)}
                for c in get_function_calls(response)
            ],
        },
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    print(f"Cached OpenRouter response: {path.name}")


def load_cached_openrouter_response(cache_key: str, request_payload: dict[str, Any]) -> Any:
    path = cache_path(cache_key)
    if not path.exists():
        raise RuntimeError(
            "CACHE MISS in replay mode.\n"
            f"No cached OpenRouter response exists for request hash:\n{cache_key}\n"
            "Replay mode refuses to contact OpenRouter."
        )
    entry = json.loads(path.read_text(encoding="utf-8"))
    if entry.get("request_hash") != cache_key or entry.get("request") != request_payload:
        raise RuntimeError(f"Cache integrity error in {path.name}")
    stored = entry.get("response", {})
    calls = [
        SimpleNamespace(name=item["name"], args=item.get("args", {}), id=item.get("id"))
        for item in stored.get("function_calls", [])
    ]
    return SimpleNamespace(text=stored.get("text", ""), function_calls=calls, candidates=[])


async def generate_openrouter(
    client: Any,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    replay: bool,
) -> Any:
    payload = {
        "provider": "openrouter",
        "model": OPENROUTER_MODEL,
        "messages": _jsonable(messages),
        "tools": _jsonable(tools),
        "temperature": 0,
    }
    cache_key = request_hash(payload)
    print(f"Request hash: {cache_key}")

    if replay:
        print("REPLAY MODE: using local cache; no OpenRouter network call.")
        return load_cached_openrouter_response(cache_key, payload)

    last_error = None
    for attempt in range(LLM_MAX_RETRIES + 1):
        try:
            response = await client.chat.completions.create(
                model=OPENROUTER_MODEL,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0,
            )
            normalized = openrouter_response_to_namespace(response)
            save_openrouter_cache_entry(cache_key, payload, normalized)
            return normalized
        except Exception as exc:
            last_error = exc
            error_text = str(exc)
            transient = any(code in error_text for code in ("429", "500", "502", "503", "504"))
            if not transient or attempt >= LLM_MAX_RETRIES:
                raise
            delay = LLM_INITIAL_BACKOFF_SECONDS * (2 ** attempt)
            print(f"OpenRouter temporary error. Retrying in {delay:.1f}s [attempt {attempt + 1}/{LLM_MAX_RETRIES}]...")
            await asyncio.sleep(delay)
    raise RuntimeError("OpenRouter request failed after retries.") from last_error


async def run_agent_openrouter(
    *, patient: str, task: str, purpose: str, subject: str, replay: bool
) -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not replay and not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set. Set it before using --provider openrouter.")
    if AsyncOpenAI is None and not replay:
        raise RuntimeError("The OpenAI SDK is not installed. Run: python -m pip install openai")

    system_prompt = load_system_prompt()
    print()
    print("==================================================")
    print("AAROGYARAKSHAK A1 AGENT")
    print("==================================================")
    print(f"Model   : {OPENROUTER_MODEL}")
    print("Provider: OpenRouter")
    print(f"Patient : {patient}")
    print(f"Purpose : {purpose}")
    print(f"Task    : {task}")
    print(f"Mode    : {'REPLAY' if replay else 'RECORD'}")
    print("==================================================")

    print("\nIssuing purpose-bound passport...")
    passport = issue_passport(subject=subject, purpose=purpose, patient=patient)
    print("Passport issued.")
    print(f"  subject : {passport['subject']}")
    print(f"  purpose : {passport['purpose']}")
    print(f"  patient : {passport['patient_id']}")

    client = None if replay else AsyncOpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers={"X-Title": "AarogyaRakshak"},
    )
    tools = build_openrouter_tools()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Patient ID: {patient}\n\nTask: {task}"},
    ]

    async with connect_to_gateway() as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as gateway_client:
            await gateway_client.initialize()
            print("\nConnected to gateway process.")
            print("Agent has NO direct EHR connection.")
            print("\nSending task to OpenRouter...")

            for turn in range(1, MAX_TURNS + 1):
                print(f"\nAgent loop turn {turn}")
                response = await generate_openrouter(
                    client, messages=messages, tools=tools, replay=replay
                )
                function_calls = get_function_calls(response)

                if not function_calls:
                    final_text = response.text or ""
                    print("\n==================================================")
                    print("FINAL AGENT RESPONSE")
                    print("==================================================")
                    print(final_text)
                    print("==================================================")
                    return final_text

                assistant_tool_calls = []
                for i, call in enumerate(function_calls):
                    assistant_tool_calls.append({
                        "id": getattr(call, "id", None) or f"call_{turn}_{i}",
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.args or {}, separators=(",", ":")),
                        },
                    })
                messages.append({
                    "role": "assistant",
                    "content": response.text or None,
                    "tool_calls": assistant_tool_calls,
                })

                for i, function_call in enumerate(function_calls):
                    gateway_result, refused = await execute_model_tool_call(
                        gateway_client, function_call, passport
                    )
                    if refused:
                        reasons = gateway_result.get("reasons", [])
                        refusal_message = "The requested action was blocked by the authorization gateway."
                        if reasons:
                            refusal_message += "\n\nReason(s):\n- " + "\n- ".join(reasons)
                        print("\nStopping agent loop because gateway refused the action.")
                        return refusal_message

                    messages.append({
                        "role": "tool",
                        "tool_call_id": getattr(function_call, "id", None) or f"call_{turn}_{i}",
                        "content": json.dumps({
                            "verdict": gateway_result.get("verdict"),
                            "result": gateway_result.get("result"),
                            "reasons": gateway_result.get("reasons", []),
                        }, ensure_ascii=False),
                    })

            raise RuntimeError(f"Agent exceeded maximum tool-call turns ({MAX_TURNS}).")


# ============================================================
# Agent loop
# ============================================================

async def run_agent(
    *,
    patient: str,
    task: str,
    purpose: str,
    subject: str,
    replay: bool,
    provider: str,
) -> str:

    if provider == "openrouter":
        return await run_agent_openrouter(
            patient=patient, task=task, purpose=purpose,
            subject=subject, replay=replay,
        )

    api_key = os.environ.get("GEMINI_API_KEY")

    if not replay and not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set in this terminal. "
            "It is not required for --replay."
        )

    system_prompt = load_system_prompt()

    print()
    print("==================================================")
    print("AAROGYARAKSHAK A1 AGENT")
    print("==================================================")
    print(f"Model   : {MODEL}")
    print(f"Patient : {patient}")
    print(f"Purpose : {purpose}")
    print(f"Task    : {task}")
    print(f"Mode    : {'REPLAY' if replay else 'RECORD'}")
    print("==================================================")

    # --------------------------------------------------------
    # Passport
    # --------------------------------------------------------

    print()
    print("Issuing purpose-bound passport...")

    passport = issue_passport(
        subject=subject,
        purpose=purpose,
        patient=patient,
    )

    print("Passport issued.")
    print(f"  subject : {passport['subject']}")
    print(f"  purpose : {passport['purpose']}")
    print(f"  patient : {passport['patient_id']}")

    # --------------------------------------------------------
    # Gemini
    # --------------------------------------------------------

    client = None if replay else genai.Client(api_key=api_key)
    tools = build_gemini_tools()

    # --------------------------------------------------------
    # Gateway
    # --------------------------------------------------------

    async with connect_to_gateway() as (
        read_stream,
        write_stream,
    ):

        async with ClientSession(
            read_stream,
            write_stream,
        ) as gateway_client:

            await gateway_client.initialize()

            print()
            print("Connected to gateway process.")
            print("Agent has NO direct EHR connection.")

            # ------------------------------------------------
            # Manual function calling
            # ------------------------------------------------

            config = types.GenerateContentConfig(
                temperature=0,
                system_instruction=system_prompt,
                tools=tools,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            )

            contents: list[Any] = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(
                            text=(
                                f"Patient ID: {patient}\n\n"
                                f"Task: {task}"
                            )
                        )
                    ],
                )
            ]

            print()
            print("Sending task to Gemini...")

            # ------------------------------------------------
            # Tool-calling loop
            # ------------------------------------------------

            for turn in range(1, MAX_TURNS + 1):

                print()
                print(f"Agent loop turn {turn}")

                response = await generate_with_retry(
                    client,
                    contents=contents,
                    config=config,
                    replay=replay,
                )

                function_calls = get_function_calls(response)

                # ------------------------------------------------
                # Final answer
                # ------------------------------------------------

                if not function_calls:

                    final_text = response.text or ""

                    print()
                    print("==================================================")
                    print("FINAL AGENT RESPONSE")
                    print("==================================================")
                    print(final_text)
                    print("==================================================")

                    return final_text

                # ------------------------------------------------
                # Preserve Gemini's function-call message
                # ------------------------------------------------

                if response.candidates:
                    model_content = response.candidates[0].content

                    if model_content is not None:
                        contents.append(model_content)

                # ------------------------------------------------
                # Execute proposed calls through gateway
                # ------------------------------------------------

                function_response_parts = []

                for function_call in function_calls:

                    gateway_result, refused = (
                        await execute_model_tool_call(
                            gateway_client,
                            function_call,
                            passport,
                        )
                    )

                    # --------------------------------------------
                    # NEVER retry or continue after BLOCK
                    # --------------------------------------------

                    if refused:

                        reasons = gateway_result.get(
                            "reasons",
                            [],
                        )

                        refusal_message = (
                            "The requested action was blocked by "
                            "the authorization gateway."
                        )

                        if reasons:
                            refusal_message += (
                                "\n\nReason(s):\n- "
                                + "\n- ".join(reasons)
                            )

                        print()
                        print(
                            "Stopping agent loop because "
                            "gateway refused the action."
                        )

                        return refusal_message

                    function_response_parts.append(
                        build_function_response_part(
                            function_call,
                            gateway_result,
                        )
                    )

                # ------------------------------------------------
                # Give gateway results back to Gemini
                # ------------------------------------------------

                contents.append(
                    types.Content(
                        role="user",
                        parts=function_response_parts,
                    )
                )

            raise RuntimeError(
                f"Agent exceeded maximum tool-call turns ({MAX_TURNS})."
            )


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description="AarogyaRakshak A1 Gemini agent"
    )

    parser.add_argument(
        "--patient",
        required=True,
        help="Patient ID, e.g. P102",
    )

    parser.add_argument(
        "--task",
        required=True,
        help="Human task for the ASHA assistant",
    )

    parser.add_argument(
        "--purpose",
        default=DEFAULT_PURPOSE,
        help=f"Passport purpose. Default: {DEFAULT_PURPOSE}",
    )

    parser.add_argument(
        "--subject",
        default=DEFAULT_SUBJECT,
        help="Agent identity.",
    )

    parser.add_argument(
        "--provider",
        choices=["gemini", "openrouter"],
        default="gemini",
        help="LLM provider. Default: gemini",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--record",
        action="store_true",
        help="Call Gemini and save every response to the local cache (default).",
    )
    mode.add_argument(
        "--replay",
        action="store_true",
        help="Use only the local cache; never call Gemini.",
    )

    return parser.parse_args()


def main() -> None:

    args = parse_args()

    try:
        asyncio.run(
            run_agent(
                patient=args.patient,
                task=args.task,
                purpose=args.purpose,
                subject=args.subject,
                replay=args.replay,
                provider=args.provider,
            )
        )

    except KeyboardInterrupt:
        print("\nAgent interrupted.")
        sys.exit(130)

    except Exception as exc:
        import traceback

        print()
        print("==================================================")
        print("AGENT ERROR")
        print("==================================================")
        print(f"{type(exc).__name__}: {exc}")
        print()
        print("FULL TRACEBACK:")
        traceback.print_exc()
        print("==================================================")
        sys.exit(1)


if __name__ == "__main__":
    main()
