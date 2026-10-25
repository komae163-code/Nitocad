"""
Plain English -> block spec.

The language model's whole job is to pick blocks from blocks.py, set their
declared parameters and name the nets that join them. It never places a
part or draws a track. Everything it returns goes through
compose.validate_spec (schema + ERC-lite); if that finds errors they are
fed back to the model for up to `max_repairs` retries, and if it still
cannot produce a valid spec the caller gets a PlanError with the reasons
- never a silently "fixed" design.

Without an API key a deterministic keyword planner covers the common
cases, so the free preview works at zero cost.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .blocks import BLOCKS, describe_library
from .compose import validate_spec


class PlanError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class PlanResult:
    spec: dict
    source: str                      # "llm" | "keywords"
    notes: list = field(default_factory=list)
    attempts: int = 1


SYSTEM_PROMPT = """You are a PCB design planner. You do NOT draw boards. You choose from a fixed library of \
pre-designed circuit blocks and describe how they connect. Reply with ONE JSON object and nothing else.

Schema:
{"title": "short board name",
 "board": {"max_width_mm": 80},
 "blocks": [{"id": "lower_snake_id", "type": "<block type>", "params": {..optional..},
             "nets": {"<PORT>": "NET_NAME", ...}}]}

Rules:
- Use ONLY the block types and params listed below. Every port of every block must appear in "nets".
- Net names are UPPERCASE letters/digits/underscore. Ports that should be electrically connected share a net name.
- Exactly one ground net (use GND) - every gnd port goes on it.
- Each power net needs exactly one power_out source; power_in ports must be on a net whose voltage is within the accepted range.
- If the request needs something the library cannot do (e.g. a buck converter, motor driver, wireless), do NOT invent \
blocks: return {"error": "explain what is missing"}.

Available blocks:
""" + "{library}"


def _system_prompt() -> str:
    return SYSTEM_PROMPT.replace("{library}", describe_library())


def _extract_json(text: str):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start:end + 1])


def llm_plan(prompt: str, call, *, max_repairs: int = 2) -> PlanResult:
    """`call(messages) -> str` is the chat-completion function (injected so
    tests need no network). Validates every reply and retries with the
    validator's own error messages."""
    messages = [{"role": "system", "content": _system_prompt()},
                {"role": "user", "content": prompt}]
    last_errors: list[str] = []
    for attempt in range(1, max_repairs + 2):
        try:
            raw = call(messages)
            data = _extract_json(raw)
        except Exception as exc:  # noqa: BLE001 - network, bad JSON
            last_errors = [f"model reply could not be read: {exc}"]
            messages += [{"role": "assistant", "content": "(unreadable)"},
                         {"role": "user", "content": "Reply with valid JSON only. " + last_errors[0]}]
            continue
        if isinstance(data, dict) and data.get("error"):
            raise PlanError([str(data["error"])[:300]])
        clean, errors, warnings = validate_spec(data)
        if not errors:
            return PlanResult(clean, "llm", notes=warnings, attempts=attempt)
        last_errors = errors
        messages += [{"role": "assistant", "content": json.dumps(data)},
                     {"role": "user", "content": "That spec was rejected:\n- " + "\n- ".join(errors[:12])
                      + "\nReturn a corrected JSON object only."}]
    raise PlanError(last_errors or ["the planner could not produce a valid design"])


# ------------------------------------------------------------- offline planner
_VOLT = re.compile(r"(\d+(?:\.\d+)?)\s*v(?:olts?)?\b")


def _has(text: str, *words: str) -> bool:
    """Word-start match, so 'led' matches 'led'/'leds' but not 'oled'."""
    return any(re.search(r"(?<![a-z0-9])" + re.escape(w), text) for w in words)


# Things people ask for that the block library cannot build - say so instead of
# quietly building something else.
_UNSUPPORTED = {
    "wifi": "Wi-Fi", "wi-fi": "Wi-Fi", "bluetooth": "Bluetooth", "ble": "Bluetooth LE", "esp32": "ESP32",
    "esp8266": "ESP8266", "lora": "LoRa", "oled": "an OLED display", "lcd": "an LCD", "display": "a display",
    "stepper": "a stepper driver", "boost": "a boost converter",
    "battery charg": "a battery charger", "usb data": "USB data", "rp2040": "an RP2040", "stm32": "an STM32",
    "audio": "audio circuitry", "amplifier": "an amplifier", "adc": "an ADC",
}


def keyword_plan(prompt: str) -> PlanResult:
    """Deterministic planner for common requests (power + 3.3 V + I2C header + LEDs +
    555 blinker + MOSFET switch + ATmega328P + breakout header). Wiring follows fixed rules."""
    t = prompt.lower()
    notes: list[str] = []
    blocks: list[dict] = []
    missing = sorted({label for key, label in _UNSUPPORTED.items() if _has(t, key)})
    if missing:
        raise PlanError([
            f"The offline planner cannot build {', '.join(missing)} yet - the library has no block "
            "for that. Supported: USB-C or screw-terminal power, 3.3 V regulator, I2C sensor header, status "
            "LED, 555 blinker, MOSFET load switch, ATmega328P microcontroller, breakout header."])

    def add(bid, btype, nets, params=None):
        d = {"id": bid, "type": btype, "nets": nets}
        if params:
            d["params"] = params
        blocks.append(d)

    wants_mcu = _has(t, "atmega", "arduino", "microcontroller", "mcu")
    wants_i2c = _has(t, "i2c", "sensor")
    wants_blink = _has(t, "555", "blink", "flash", "oscillat")
    wants_led = _has(t, "led") and not wants_blink
    wants_switch = _has(t, "mosfet", "switch", "load")
    wants_break = _has(t, "breakout", "header", "test point")
    needs_3v3 = _has(t, "3.3", "3v3") or wants_i2c or wants_mcu

    if not any([wants_mcu, wants_i2c, wants_blink, wants_led, wants_switch, wants_break]):
        raise PlanError([
            "Could not map that request to the available circuit blocks. Without an AI planner I can "
            "recognise: power input (USB-C or screw terminal), 3.3 V regulator, I2C sensor header, "
            "status LED, 555 blinker, MOSFET load switch, ATmega328P microcontroller, breakout header."])

    # Later statements win: Studio joins a chat's messages with " | " and an edit such as
    # "use a 12V terminal instead" must override what was said first.
    segments = [seg for seg in re.split(r"\s*\|\s*", t) if seg.strip()] or [t]
    vals: list[float] = []
    for seg in reversed(segments):
        vals = [float(m.group(1)) for m in _VOLT.finditer(seg)]
        if any(v >= 4.5 for v in vals):
            break
    volts = next((v for v in vals if 4.5 <= v <= 60.0), 5.0)
    target = next((v for v in vals if 1.5 <= v <= 20.0 and v < volts and v != volts), None)
    last_usb = max((m.start() for m in re.finditer(r"usb", t)), default=-1)
    last_terminal = max((m.start() for m in re.finditer(r"terminal|screw|barrel|battery|dc jack|wall adapter|external", t)),
                        default=-1)
    if last_usb > last_terminal:
        add("pwr", "power_in_usbc", {"VBUS": "VBUS", "GND": "GND"})
        raw = "VBUS"
        volts = 5.0
    else:
        add("pwr", "power_in_terminal", {"VIN": "VIN", "GND": "GND"}, {"voltage": volts})
        raw = "VIN"
    rail = raw
    wants_buck = _has(t, "buck", "step down", "step-down", "stepdown")
    needs_low_rail = needs_3v3 or wants_i2c or wants_mcu or wants_led or (wants_blink and volts > 15.0)
    if volts > 6.0 and (wants_buck or needs_low_rail):
        vout = target if target else (3.3 if needs_3v3 else 5.0)
        add("buck", "buck_lm2596", {"IN": raw, "OUT": "VBUCK", "GND": "GND"}, {"vout": vout})
        rail = "VBUCK"
        notes.append(f"{volts:g} V input is stepped down to {vout:g} V by an LM2596 buck converter.")
    elif needs_3v3:
        add("reg", "ldo_3v3", {"VIN": raw, "VOUT": "V3V3", "GND": "GND"})
        rail = "V3V3"
    if wants_mcu:
        add("mcu", "mcu_atmega328p", {"V": rail, "GND": "GND", "SDA": "SDA", "SCL": "SCL",
                                      "IO1": "IO1", "IO2": "IO2", "IO3": "IO3"})
    if wants_i2c:
        add("i2c", "i2c_sensor_header", {"V": rail, "GND": "GND", "SDA": "SDA", "SCL": "SCL"})
    if wants_blink:
        if wants_mcu:
            add("blink", "led_from_signal", {"SIG": "IO2", "GND": "GND"})
            notes.append("Blink LED is driven by the microcontroller's IO2 pin (firmware makes it blink).")
        else:
            add("osc", "tlc555_blinker", {"V": rail, "GND": "GND", "OUT": "BLINK"})
            add("blink", "led_from_signal", {"SIG": "BLINK", "GND": "GND"})
    if wants_led:
        add("led", "led_indicator", {"V": rail, "GND": "GND"})
    gate_floating = False
    if wants_switch:
        gate = "IO1" if wants_mcu else "GATE"
        add("sw", "mosfet_switch", {"V": raw, "GND": "GND", "GATE": gate})
        if not wants_mcu:
            gate_floating = True
            wants_break = True
            notes.append("MOSFET gate (net GATE) is not driven by anything on this board - wire it to your "
                         "controller via the breakout header.")
    if _has(t, "motor", "relay", "solenoid", "coil"):
        notes.append("Inductive loads (motors, relays) need a flyback diode across the load - not included.")
    if wants_break:
        extra = (["GATE"] if gate_floating else []) + (["IO1", "IO2", "IO3"] if wants_mcu else [])
        names = extra[:8] if extra else [rail, "SIGNAL1", "SIGNAL2"]
        nets = {"GND": "GND"}
        for i, n in enumerate(names):
            nets[f"P{i + 1}"] = n
        add("brk", "header_breakout", nets, {"pins": len(names)})

    title = re.sub(r"\s+", " ", prompt.strip())[:60] or "NitoCAD board"
    spec = {"title": title, "board": {"max_width_mm": 80}, "blocks": blocks}
    clean, errors, warnings = validate_spec(spec)
    if errors:
        raise PlanError(errors)
    return PlanResult(clean, "keywords", notes=notes + warnings)


def plan(prompt: str, call=None, *, max_repairs: int = 2) -> PlanResult:
    """LLM planner when a chat function is supplied, keyword planner otherwise."""
    if call is None:
        return keyword_plan(prompt)
    return llm_plan(prompt, call, max_repairs=max_repairs)


def deepseek_call(api_key: str, base_url: str, model: str, timeout: float = 30.0):
    """Build the `call(messages)` function for DeepSeek's OpenAI-compatible API,
    mirroring deepseek_parser.py. Imported lazily so the pack stays stdlib-only."""
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def call(messages):
        resp = client.chat.completions.create(model=model, messages=messages, temperature=0,
                                              response_format={"type": "json_object"})
        return resp.choices[0].message.content

    return call
