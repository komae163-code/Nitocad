"""
Pre-designed circuit blocks.

This is the part that decides whether generated boards can be trusted: every
block is a small reference circuit with a fixed pin map, decoupling and
hand-set relative placement, and the AI planner can only pick blocks and set
the parameters declared here.

STATUS: no block has been built and powered on real hardware yet, and pin maps
were written without a human cross-check against the physical datasheets.
BLOCKS_HARDWARE_PROVEN below is False for that reason; while it is False every
board report carries a warning. Flip it only after a prototype has been built.

Roadmap (not built yet): MCU cores (RP2040 / ESP32-C3), boost converters,
USB-UART, motor drivers, level shifters, battery chargers.

Blocks marked review_required carry pin maps that were NOT cross-checked
against a physical datasheet by the pack's author - a human must verify them
before boards are ordered. The report surfaces that warning on every board.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Flip to True only after a prototype using these blocks has been built and powered on.
BLOCKS_HARDWARE_PROVEN = False

E24 = [10, 11, 12, 13, 15, 16, 18, 20, 22, 24, 27, 30, 33, 36, 39, 43, 47, 51, 56, 62, 68, 75, 82, 91]


@dataclass
class PartSpec:
    prefix: str                 # R, C, U, J, D
    value: str
    lib_id: str
    pins: dict                  # pad number -> net name
    x: float = 0.0
    y: float = 0.0
    rot: int = 0
    mpn: str | None = None
    review: bool = False
    near: tuple | None = None   # (part_index, side, along_mm): place just outside that part's bbox
    alt: tuple = ()             # fallback footprint ids tried in order if lib_id is not in the library


@dataclass
class Port:
    role: str                   # power_out | power_in | gnd | io
    voltage: float | None = None    # power_out: volts it provides
    vmin: float | None = None       # power_in: acceptable range
    vmax: float | None = None


@dataclass
class Ctx:
    block_id: str
    voltages: dict = field(default_factory=dict)   # global net -> volts (from power_out ports)

    def internal(self, name: str) -> str:
        return f"{self.block_id}.{name}"


@dataclass
class Block:
    type: str
    title: str
    description: str
    ports: dict                 # port name -> Port  (may depend on params via ports_fn)
    params: dict                # param name -> default
    build: object               # fn(ctx, nets, params) -> list[PartSpec]
    review_required: bool = False
    notes: str = ""
    param_help: dict = field(default_factory=dict)
    ports_fn: object = None     # optional fn(params) -> ports dict (for params that change ports)
    currents: object = None     # optional fn(ctx, nets, params) -> {net name: amps} for nets that must be wide


def nearest_e24(ohms: float) -> int:
    best = None
    for dec in (1, 10, 100, 1000, 10000, 100000):
        for v in E24:
            r = v * dec / 10
            if best is None or abs(r - ohms) < abs(best - ohms):
                best = r
    return int(best)


def fmt_ohms(r: int) -> str:
    if r >= 1_000_000:
        return f"{r/1_000_000:g}M"
    if r >= 1000:
        return f"{r/1000:g}k"
    return f"{r:g}"


# ----------------------------------------------------------------- builders
def _terminal(ctx, nets, p):
    return [PartSpec("J", "Screw terminal 5.08mm", "nitocad:TerminalBlock_1x02_P5.08mm",
                     {"1": nets["VIN"], "2": nets["GND"]})]


def _usbc(ctx, nets, p):
    cc1, cc2 = ctx.internal("CC1"), ctx.internal("CC2")
    return [
        PartSpec("J", "USB-C receptacle (power only)",
                 "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMount_Horizontal",
                 {"A1B12": nets["GND"], "B1A12": nets["GND"], "A4B9": nets["VBUS"],
                  "B4A9": nets["VBUS"], "A5": cc1, "B5": cc2, "S1": nets["GND"]},
                 review=True),
        PartSpec("R", "5.1k", "Resistor_SMD:R_0603_1608Metric", {"1": cc1, "2": nets["GND"]},
                 near=(0, "below", -2.0)),
        PartSpec("R", "5.1k", "Resistor_SMD:R_0603_1608Metric", {"1": cc2, "2": nets["GND"]},
                 near=(0, "below", 2.0)),
    ]


def _ldo(ctx, nets, p):
    return [
        PartSpec("U", "AP2112K-3.3", "Package_TO_SOT_SMD:SOT-23-5",
                 {"1": nets["VIN"], "2": nets["GND"], "3": nets["VIN"], "5": nets["VOUT"]},
                 mpn="AP2112K-3.3TRG1"),
        PartSpec("C", "1uF", "Capacitor_SMD:C_0603_1608Metric",
                 {"1": nets["VIN"], "2": nets["GND"]}, x=-3.6, y=0, rot=90),
        PartSpec("C", "1uF", "Capacitor_SMD:C_0603_1608Metric",
                 {"1": nets["VOUT"], "2": nets["GND"]}, x=3.6, y=0, rot=90),
    ]


def _i2c_header(ctx, nets, p):
    parts = [PartSpec("J", "I2C sensor header", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
                      {"1": nets["V"], "2": nets["GND"], "3": nets["SCL"], "4": nets["SDA"]})]
    if p["pullups"]:
        val = fmt_ohms(int(p["pullup_ohms"]))
        parts.append(PartSpec("R", val, "Resistor_SMD:R_0603_1608Metric",
                              {"1": nets["V"], "2": nets["SCL"]}, x=-3.6, y=5.08))
        parts.append(PartSpec("R", val, "Resistor_SMD:R_0603_1608Metric",
                              {"1": nets["V"], "2": nets["SDA"]}, x=-3.6, y=7.62))
    return parts


def _led(ctx, nets, p):
    v = ctx.voltages.get(nets["V"])
    ohms = p["resistor_ohms"]
    if not ohms:
        ohms = nearest_e24(((v if v else 3.3) - 2.0) / 0.002)   # ~2 mA, fine for a status LED
    anode = ctx.internal("A")
    return [
        PartSpec("R", fmt_ohms(int(ohms)), "Resistor_SMD:R_0603_1608Metric",
                 {"1": nets["V"], "2": anode}),
        PartSpec("D", "LED", "LED_SMD:LED_0603_1608Metric",
                 {"1": nets["GND"], "2": anode}, x=3.4, y=0, rot=180),
    ]


class BlockParamError(ValueError):
    """A block parameter is out of the range the block was verified for."""


def fmt_cap(nf: float) -> str:
    if nf >= 1000:
        return f"{nf / 1000:g}uF"
    return f"{nf:g}nF"


def _hdr_lib(n: int) -> str:
    return f"Connector_PinHeader_2.54mm:PinHeader_1x{n:02d}_P2.54mm_Vertical"


def _breakout_ports(params) -> dict:
    n = int(params["pins"])
    ports = {"GND": Port("gnd")}
    for i in range(1, n + 1):
        ports[f"P{i}"] = Port("io")
    return ports


def _breakout(ctx, nets, p):
    n = int(p["pins"])
    if not 1 <= n <= 11:
        raise BlockParamError("header_breakout.pins must be between 1 and 11")
    pins = {"1": nets["GND"]}
    for i in range(1, n + 1):
        pins[str(i + 1)] = nets[f"P{i}"]
    return [PartSpec("J", f"{n + 1}-pin breakout header (GND + {n} signals)", _hdr_lib(n + 1), pins)]


def _tlc555(ctx, nets, p):
    r1 = nearest_e24(p["r1_ohms"])
    r2 = nearest_e24(p["r2_ohms"])
    c_nf = float(p["timing_cap_nf"])
    if not 1000 <= r1 <= 1_000_000 or not 1000 <= r2 <= 10_000_000:
        raise BlockParamError("tlc555_blinker: r1/r2 must be 1k-1M / 1k-10M ohms")
    if not 10 <= c_nf <= 10000:
        raise BlockParamError("tlc555_blinker: timing_cap_nf must be 10-10000 (10 nF - 10 uF)")
    th, dis, ctl = ctx.internal("TH"), ctx.internal("DIS"), ctx.internal("CTL")
    v, g = nets["V"], nets["GND"]
    chip = "Capacitor_SMD:C_0603_1608Metric"
    res = "Resistor_SMD:R_0603_1608Metric"
    return [
        PartSpec("U", "TLC555", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                 {"1": g, "2": th, "3": nets["OUT"], "4": v, "5": ctl, "6": th, "7": dis, "8": v},
                 mpn="TLC555CDR"),
        PartSpec("R", fmt_ohms(r1), res, {"1": v, "2": dis}, near=(0, "below", -6.4)),
        PartSpec("R", fmt_ohms(r2), res, {"1": dis, "2": th}, near=(0, "below", -3.2)),
        PartSpec("C", fmt_cap(c_nf), chip, {"1": th, "2": g}, near=(0, "below", 0.0)),
        PartSpec("C", "10nF", chip, {"1": ctl, "2": g}, near=(0, "below", 3.2)),
        PartSpec("C", "100nF", chip, {"1": v, "2": g}, near=(0, "below", 6.4)),
    ]


def _led_from_signal(ctx, nets, p):
    ohms = p["resistor_ohms"]
    if not 100 <= ohms <= 100_000:
        raise BlockParamError("led_from_signal.resistor_ohms must be 100-100000")
    anode = ctx.internal("A")
    return [
        PartSpec("R", fmt_ohms(int(nearest_e24(ohms))), "Resistor_SMD:R_0603_1608Metric",
                 {"1": nets["SIG"], "2": anode}),
        PartSpec("D", "LED", "LED_SMD:LED_0603_1608Metric",
                 {"1": nets["GND"], "2": anode}, x=3.4, y=0, rot=180),
    ]


def _mosfet_currents(ctx, nets, p):
    amps = float(p["load_a"])
    return {nets["V"]: amps, ctx.internal("DRAIN"): amps}


def _mosfet_switch(ctx, nets, p):
    if not 0.05 <= float(p["load_a"]) <= 2.0:
        raise BlockParamError("mosfet_switch.load_a must be between 0.05 and 2.0 amps")
    drain = ctx.internal("DRAIN")
    return [
        PartSpec("Q", "AO3400A", "Package_TO_SOT_SMD:SOT-23",
                 {"1": nets["GATE"], "2": nets["GND"], "3": drain}, mpn="AO3400A"),
        PartSpec("R", "100k", "Resistor_SMD:R_0603_1608Metric",
                 {"1": nets["GATE"], "2": nets["GND"]}, near=(0, "below", -2.2)),
        PartSpec("J", "Load connector (1 = supply, 2 = switched return)", _hdr_lib(2),
                 {"1": nets["V"], "2": drain}, near=(0, "right", 0.0)),
    ]


def _atmega328p(ctx, nets, p):
    rst, sck, miso, mosi, aref = (ctx.internal(n) for n in ("RST", "SCK", "MISO", "MOSI", "AREF"))
    v, g = nets["V"], nets["GND"]
    chip = "Capacitor_SMD:C_0603_1608Metric"
    return [
        PartSpec("U", "ATmega328P-AU", "Package_QFP:TQFP-32_7x7mm_P0.8mm",
                 {"4": v, "6": v, "18": v, "3": g, "5": g, "21": g, "20": aref, "29": rst,
                  "17": sck, "16": miso, "15": mosi, "27": nets["SDA"], "28": nets["SCL"],
                  "9": nets["IO1"], "10": nets["IO2"], "13": nets["IO3"]},
                 mpn="ATMEGA328P-AU", review=True),
        PartSpec("C", "100nF", chip, {"1": v, "2": g}, near=(0, "above", -5.0)),
        PartSpec("C", "100nF", chip, {"1": v, "2": g}, near=(0, "above", -1.6)),
        PartSpec("C", "100nF", chip, {"1": v, "2": g}, near=(0, "above", 1.6)),
        PartSpec("C", "100nF", chip, {"1": aref, "2": g}, near=(0, "above", 5.0)),
        PartSpec("R", "10k", "Resistor_SMD:R_0603_1608Metric", {"1": v, "2": rst}, near=(0, "below", -3.0)),
        PartSpec("J", "ISP programming header 2x3",
                 "Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical",
                 {"1": miso, "2": v, "3": sck, "4": mosi, "5": rst, "6": g}, near=(0, "below", 4.0)),
    ]


LM2596_VREF = 1.23          # V, typical internal reference of the adjustable LM2596
LM2596_FSW = 150e3          # Hz, fixed switching frequency
_L_STANDARD_UH = (22, 33, 47, 68, 100, 150, 220)


def _lm2596_divider(vout: float) -> tuple[int, int, float]:
    """Pick the E24 pair (R1 1k-5.1k, R2) whose Vout = 1.23*(1+R2/R1) lands closest to the target."""
    best = None
    for r1 in (v * 100 for v in E24 if 10 <= v <= 51):
        for dec in (10, 100, 1000, 10000):
            for v in E24:
                r2 = v * dec / 10
                if not 500 <= r2 <= 1_000_000:
                    continue
                actual = LM2596_VREF * (1 + r2 / r1)
                err = abs(actual - vout)
                if best is None or err < best[0] - 1e-9:
                    best = (err, int(r1), int(r2), actual)
    return best[1], best[2], best[3]


def _buck_check(p):
    vout, iout = float(p["vout"]), float(p["iout_max_a"])
    if not 1.5 <= vout <= 20.0:
        raise BlockParamError("buck_lm2596.vout must be between 1.5 and 20 V")
    if not 0.1 <= iout <= 2.0:
        raise BlockParamError("buck_lm2596.iout_max_a must be between 0.1 and 2.0 A")
    return vout, iout


def _buck_ports(params) -> dict:
    vout = float(params["vout"])
    actual = _lm2596_divider(vout)[2] if 1.5 <= vout <= 20.0 else vout
    return {
        "IN": Port("power_in", vmin=max(4.5, round(vout + 2.0, 2)), vmax=30.0),
        "GND": Port("gnd"),
        "OUT": Port("power_out", voltage=round(actual, 2)),
    }


def _buck_design(ctx, nets, p):
    """All derived values in one place (used by the builder, the current hook and the report)."""
    import math
    vout, iout = _buck_check(p)
    r1, r2, actual = _lm2596_divider(vout)
    vin = float(ctx.voltages.get(nets["IN"], 24.0)) * 1.1          # +10 % supply tolerance
    vin = max(vin, vout + 2.0)
    duty = actual / vin
    l_req_uh = (vin - actual) * duty / (LM2596_FSW * 0.4 * iout) * 1e6      # 40 % ripple at full load
    l_uh = next((v for v in _L_STANDARD_UH if v >= l_req_uh), None)
    if l_uh is None:
        raise BlockParamError("buck_lm2596: this input/output/current combination needs an inductor "
                              "larger than 220 uH - lower the input voltage or raise iout_max_a")
    ripple = (vin - actual) * duty / (LM2596_FSW * l_uh * 1e-6)
    isat = math.ceil((iout + ripple / 2) * 1.25 * 10) / 10                  # 25 % saturation margin
    iin = iout * actual / (vin / 1.1) / 0.8                                  # avg input current at 80 % efficiency
    return {"vout": vout, "iout": iout, "r1": r1, "r2": r2, "actual": actual, "l_uh": l_uh,
            "isat": isat, "iin": iin}


def _buck_currents(ctx, nets, p):
    d = _buck_design(ctx, nets, p)
    return {nets["IN"]: d["iin"], ctx.internal("SW"): d["iout"], nets["OUT"]: d["iout"]}


def _buck_lm2596(ctx, nets, p):
    d = _buck_design(ctx, nets, p)
    sw, fb = ctx.internal("SW"), ctx.internal("FB")
    vin_n, vout_n, g = nets["IN"], nets["OUT"], nets["GND"]
    small_cap = d["actual"] <= 12.0
    cout_fp = "Capacitor_THT:CP_Radial_D8.0mm_P3.50mm" if small_cap else "Capacitor_THT:CP_Radial_D10.0mm_P5.00mm"
    cout_alt = ("Capacitor_THT:CP_Radial_D8.0mm_P3.80mm",) if small_cap else ("Capacitor_THT:CP_Radial_D10.0mm_P5.00mm_P2.50mm",)
    res = "Resistor_SMD:R_0603_1608Metric"
    return [
        # LM2596S-ADJ, TO-263-5. Pins: 1 VIN, 2 OUTPUT (switch node), 3 GND (= tab), 4 FEEDBACK, 5 ON/OFF (low = on)
        PartSpec("U", "LM2596S-ADJ", "Package_TO_SOT_SMD:TO-263-5_TabPin3",
                 {"1": vin_n, "2": sw, "3": g, "4": fb, "5": g}, mpn="LM2596S-ADJ/NOPB", review=True),
        PartSpec("C", "470uF 50V", "Capacitor_THT:CP_Radial_D12.5mm_P5.00mm", {"1": vin_n, "2": g},
                 alt=("Capacitor_THT:CP_Radial_D13.0mm_P5.00mm",), near=(0, "left", 0.0)),
        PartSpec("L", f"{d['l_uh']}uH Isat>={d['isat']:g}A", "Inductor_SMD:L_Bourns-SRR1260", {"1": sw, "2": vout_n},
                 alt=("Inductor_SMD:L_12x12mm_H8mm", "Inductor_SMD:L_12x12mm_H6mm"), near=(0, "right", 0.0)),
        PartSpec("C", "220uF " + ("25V" if small_cap else "35V"), cout_fp, {"1": vout_n, "2": g},
                 alt=cout_alt, near=(2, "right", 0.0)),
        # catch (freewheel) diode: cathode to the switch node, anode to ground. SS34 = 40 V, 3 A Schottky.
        PartSpec("D", "SS34", "Diode_SMD:D_SMA", {"1": sw, "2": g}, mpn="SS34", near=(0, "below", 0.0)),
        PartSpec("R", fmt_ohms(d["r1"]), res, {"1": fb, "2": g}, near=(4, "below", -1.8)),
        PartSpec("R", fmt_ohms(d["r2"]), res, {"1": vout_n, "2": fb}, near=(4, "below", 1.8)),
    ]


BLOCKS: dict[str, Block] = {
    "power_in_terminal": Block(
        "power_in_terminal", "Screw-terminal power input",
        "2-pin 5.08 mm screw terminal bringing external DC power onto the board.",
        {"VIN": Port("power_out", voltage=5.0), "GND": Port("gnd")},
        {"voltage": 5.0}, _terminal,
        param_help={"voltage": "Nominal input voltage in volts (used for safety checks)"},
    ),
    "power_in_usbc": Block(
        "power_in_usbc", "USB-C 5 V power input",
        "USB-C receptacle (power only) with the two 5.1 k CC pull-downs that make a "
        "USB-C source supply 5 V.",
        {"VBUS": Port("power_out", voltage=5.0), "GND": Port("gnd")},
        {}, _usbc, review_required=True,
        notes="Uses the KiCad library footprint; pad-to-signal mapping must be checked "
              "against the connector datasheet before ordering boards.",
    ),
    "ldo_3v3": Block(
        "ldo_3v3", "3.3 V LDO regulator",
        "AP2112K-3.3 low-dropout regulator, 600 mA, with 1 uF input and output capacitors. "
        "Input must be 3.6-6.0 V.",
        {"VIN": Port("power_in", vmin=3.6, vmax=6.0), "VOUT": Port("power_out", voltage=3.3),
         "GND": Port("gnd")},
        {}, _ldo,
    ),
    "i2c_sensor_header": Block(
        "i2c_sensor_header", "I2C sensor header",
        "4-pin header (V, GND, SCL, SDA) for an off-board I2C sensor module, with optional "
        "pull-up resistors.",
        {"V": Port("power_in", vmin=1.8, vmax=5.5), "GND": Port("gnd"),
         "SDA": Port("io"), "SCL": Port("io")},
        {"pullups": True, "pullup_ohms": 4700}, _i2c_header,
        param_help={"pullups": "Fit SDA/SCL pull-ups (turn off if the module has them)",
                    "pullup_ohms": "Pull-up value in ohms"},
    ),
    "led_indicator": Block(
        "led_indicator", "Status LED",
        "LED with series resistor, sized automatically for the supply voltage (about 2 mA).",
        {"V": Port("power_in", vmin=2.5, vmax=24.0), "GND": Port("gnd")},
        {"resistor_ohms": 0}, _led,
        param_help={"resistor_ohms": "Series resistor in ohms; 0 = choose automatically"},
    ),
    "header_breakout": Block(
        "header_breakout", "Signal breakout header",
        "Single-row 2.54 mm header exposing GND plus N signal nets (pins=1-11) so you can wire "
        "off-board modules or test equipment to any net.",
        _breakout_ports({"pins": 4}), {"pins": 4}, _breakout, ports_fn=_breakout_ports,
        param_help={"pins": "Number of signal pins (1-11); ports are named P1..Pn"},
    ),
    "tlc555_blinker": Block(
        "tlc555_blinker", "TLC555 astable oscillator",
        "CMOS 555 timer (2-15 V) in astable mode: output OUT toggles at about "
        "1.44 / ((R1 + 2*R2) * C) Hz. Defaults give roughly 7 Hz. Output is a logic-level signal "
        "(drive an LED through the led_from_signal block).",
        {"V": Port("power_in", vmin=2.0, vmax=15.0), "GND": Port("gnd"), "OUT": Port("io")},
        {"r1_ohms": 1000, "r2_ohms": 100000, "timing_cap_nf": 1000}, _tlc555,
        param_help={"r1_ohms": "R1 in ohms (1k-1M)", "r2_ohms": "R2 in ohms (1k-10M)",
                    "timing_cap_nf": "Timing capacitor in nF (10-10000)"},
    ),
    "led_from_signal": Block(
        "led_from_signal", "LED driven by a signal net",
        "LED with series resistor from a signal net SIG to ground. Default 1 k suits signals up "
        "to about 5 V (about 3 mA at 5 V); raise resistor_ohms for higher-voltage signals.",
        {"SIG": Port("io"), "GND": Port("gnd")}, {"resistor_ohms": 1000}, _led_from_signal,
        param_help={"resistor_ohms": "Series resistor in ohms (100-100000)"},
    ),
    "mosfet_switch": Block(
        "mosfet_switch", "N-MOSFET low-side switch",
        "AO3400A logic-level N-MOSFET switching the ground side of a load, with a 100 k gate "
        "pull-down and a 2-pin load connector. Port V is the LOAD supply (3-24 V). Set load_a "
        "(0.05-2 A) to your load current: the supply and load nets are then routed with "
        "wide tracks sized for it. No flyback diode - do not drive motors or relays without "
        "adding one externally.",
        {"V": Port("power_in", vmin=3.0, vmax=24.0), "GND": Port("gnd"), "GATE": Port("io")},
        {"load_a": 0.5}, _mosfet_switch, currents=_mosfet_currents,
        param_help={"load_a": "Maximum load current in amps (0.05-2.0); sets power-track width"},
    ),
    "mcu_atmega328p": Block(
        "mcu_atmega328p", "ATmega328P microcontroller (internal oscillator)",
        "ATmega328P-AU with decoupling, reset pull-up and a 6-pin ISP programming header. Runs from "
        "its internal 8 MHz RC oscillator (no crystal). Exposes I2C (SDA, SCL) and three GPIOs "
        "(IO1, IO2, IO3). Supply 2.7-5.5 V. Needs an ISP programmer to load firmware.",
        {"V": Port("power_in", vmin=2.7, vmax=5.5), "GND": Port("gnd"), "SDA": Port("io"),
         "SCL": Port("io"), "IO1": Port("io"), "IO2": Port("io"), "IO3": Port("io")},
        {}, _atmega328p, review_required=True,
        notes="ATmega328P TQFP-32 pin map and the ISP header mapping were written without a "
              "physical datasheet cross-check; verify against the Microchip datasheet before "
              "ordering boards. Requires KiCad's footprint library (TQFP-32).",
    ),
    "buck_lm2596": Block(
        "buck_lm2596", "LM2596 step-down (buck) converter",
        "Adjustable buck converter (LM2596S-ADJ, 150 kHz): converts a higher input (vout+2 V up to "
        "30 V, e.g. 12 V or 24 V) down to the output voltage vout (1.5-20 V) at up to iout_max_a "
        "(0.1-2 A). Feedback divider and inductor are calculated from vout, the input voltage and the "
        "current. Uses a large through-hole input capacitor, so it makes a bigger board. Port OUT is "
        "the regulated rail; the input and output nets are routed with wide tracks.",
        {"IN": Port("power_in", vmin=7.0, vmax=30.0), "GND": Port("gnd"), "OUT": Port("power_out", voltage=5.0)},
        {"vout": 5.0, "iout_max_a": 1.0}, _buck_lm2596, ports_fn=_buck_ports, currents=_buck_currents,
        review_required=True,
        notes="Pin map (1 VIN, 2 OUT, 3 GND/tab, 4 FB, 5 ON/OFF) and the typical-application values were "
              "written without a physical datasheet cross-check, and footprints come from KiCad's library "
              "(TO-263-5, SMA, radial caps, 12 mm inductor). Verify against the TI LM2596 datasheet, "
              "check inductor saturation current and capacitor ripple rating, and check the tab's "
              "thermal copper before ordering. Output accuracy is about +/-3 % (internal reference).",
        param_help={"vout": "Output voltage in volts (1.5-20)", "iout_max_a": "Maximum load current in amps (0.1-2)"},
    ),
}


def describe_library() -> str:
    """Text for the planner prompt - generated from the library so it can't drift."""
    lines = []
    for b in BLOCKS.values():
        ports = ", ".join(
            f"{n}({pt.role}" + (f" {pt.voltage}V" if pt.voltage else "")
            + (f" accepts {pt.vmin}-{pt.vmax}V" if pt.vmin is not None else "") + ")"
            for n, pt in b.ports.items())
        params = ", ".join(f"{k}={v!r}" for k, v in b.params.items()) or "none"
        lines.append(f"- {b.type}: {b.description} Ports: {ports}. Params: {params}.")
    return "\n".join(lines)
