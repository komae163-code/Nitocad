"""Spec -> checked netlist (unplaced Design).

Includes a small electrical rule check (ERC-lite): power sources and
consumers must match, voltages must be inside each block's accepted range,
and there is exactly one ground. It cannot prove a circuit works - it
catches the mistakes a block-composing planner can actually make.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .blocks import BLOCKS, BlockParamError, Ctx
from .footprints import FootprintError, resolve
from .model import RULES, Component, Design, width_for_current

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
NET_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,23}$")
MAX_BLOCKS = 24


class SpecError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _ports_for(block, params):
    return block.ports_fn(params) if block.ports_fn else block.ports


def validate_spec(spec: dict) -> tuple[dict, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(spec, dict):
        return {}, ["spec must be a JSON object"], []
    blocks_in = spec.get("blocks")
    if not isinstance(blocks_in, list) or not blocks_in:
        return {}, ["spec needs a non-empty 'blocks' list"], []
    if len(blocks_in) > MAX_BLOCKS:
        return {}, [f"too many blocks (max {MAX_BLOCKS})"], []

    clean_blocks, seen = [], set()
    for i, b in enumerate(blocks_in):
        if not isinstance(b, dict):
            errors.append(f"block #{i + 1} is not an object")
            continue
        bid, btype = b.get("id"), b.get("type")
        if not isinstance(bid, str) or not ID_RE.match(bid):
            errors.append(f"block #{i + 1}: id must be lowercase letters/digits/underscore, got {bid!r}")
            continue
        if bid in seen:
            errors.append(f"duplicate block id {bid!r}")
            continue
        seen.add(bid)
        block = BLOCKS.get(btype)
        if block is None:
            errors.append(f"block {bid!r}: unknown type {btype!r}. Available: {', '.join(BLOCKS)}")
            continue
        params = dict(block.params)
        user_params = b.get("params") or {}
        if not isinstance(user_params, dict):
            errors.append(f"block {bid!r}: params must be an object")
            continue
        for k, v in user_params.items():
            if k not in block.params:
                errors.append(f"block {bid!r}: unknown param {k!r} (allowed: {', '.join(block.params) or 'none'})")
                continue
            default = block.params[k]
            if isinstance(default, bool):
                if not isinstance(v, bool):
                    errors.append(f"block {bid!r}: param {k!r} must be true/false")
                    continue
            elif isinstance(default, (int, float)):
                if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or v > 1e7:
                    errors.append(f"block {bid!r}: param {k!r} must be a non-negative number")
                    continue
            params[k] = v
        ports = _ports_for(block, params)
        nets_in = b.get("nets") or {}
        if not isinstance(nets_in, dict):
            errors.append(f"block {bid!r}: nets must be an object")
            continue
        nets = {}
        for port in ports:
            net = nets_in.get(port)
            if net is None:
                errors.append(f"block {bid!r}: port {port!r} is not connected to a net")
            elif not isinstance(net, str) or not NET_RE.match(net):
                errors.append(f"block {bid!r}: net name {net!r} must be UPPERCASE letters/digits/underscore")
            else:
                nets[port] = net
        for extra in nets_in:
            if extra not in ports:
                errors.append(f"block {bid!r}: has no port {extra!r} (ports: {', '.join(ports)})")
        clean_blocks.append({"id": bid, "type": btype, "params": params, "nets": nets})

    title = str(spec.get("title") or "Untitled board")[:80]
    board_in = spec.get("board") if isinstance(spec.get("board"), dict) else {}
    max_w = board_in.get("max_width_mm", 80)
    if not isinstance(max_w, (int, float)) or isinstance(max_w, bool) or not 20 <= max_w <= 200:
        errors.append("board.max_width_mm must be between 20 and 200")
        max_w = 80
    if errors:
        return {}, errors, warnings
    clean = {"title": title, "blocks": clean_blocks, "board": {"max_width_mm": float(max_w)}}
    e2, w2 = erc_lite(clean)
    return clean, e2, warnings + w2


def _block_voltage(binst, port_name, port):
    if port.role == "power_out":
        if binst["type"] == "power_in_terminal" and port_name == "VIN":
            return float(binst["params"].get("voltage", port.voltage))
        return port.voltage
    return None


def net_voltages(spec: dict) -> dict:
    volts = {}
    for binst in spec["blocks"]:
        block = BLOCKS[binst["type"]]
        for pname, port in _ports_for(block, binst["params"]).items():
            v = _block_voltage(binst, pname, port)
            if v is not None and pname in binst["nets"]:
                volts[binst["nets"][pname]] = v
    return volts


def erc_lite(spec: dict) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    sources = defaultdict(list)
    consumers = defaultdict(list)
    gnd_nets = set()
    volts = net_voltages(spec)
    for binst in spec["blocks"]:
        block = BLOCKS[binst["type"]]
        for pname, port in _ports_for(block, binst["params"]).items():
            net = binst["nets"].get(pname)
            if net is None:
                continue
            if port.role == "power_out":
                sources[net].append(f"{binst['id']}.{pname}")
            elif port.role == "power_in":
                consumers[net].append((binst["id"], pname, port))
            elif port.role == "gnd":
                gnd_nets.add(net)
    if not gnd_nets:
        errors.append("the design has no ground connection")
    elif len(gnd_nets) > 1:
        errors.append(f"multiple ground nets {sorted(gnd_nets)} - only a single shared ground is supported")
    for net, outs in sources.items():
        if len(outs) > 1:
            errors.append(f"net {net} is driven by more than one source ({', '.join(outs)}) - that is a short circuit")
    for net, cons in consumers.items():
        if net not in sources:
            who = ", ".join(f"{b}.{p}" for b, p, _ in cons)
            errors.append(f"net {net} powers {who} but nothing supplies it")
            continue
        v = volts.get(net)
        for bid, pname, port in cons:
            if v is not None and port.vmin is not None and v < port.vmin:
                errors.append(f"{bid}.{pname} needs at least {port.vmin} V but net {net} is {v} V")
            if v is not None and port.vmax is not None and v > port.vmax:
                is_buck = next((b["type"] for b in spec["blocks"] if b["id"] == bid), "") == "buck_lm2596"
                hint = ("that is this converter's input limit" if is_buck
                        else "add a buck_lm2596 block to step the voltage down first")
                errors.append(f"{bid}.{pname} accepts at most {port.vmax} V but net {net} is {v} V ({hint})")
    for net in sources:
        if net not in consumers and net not in gnd_nets:
            warnings.append(f"net {net} is supplied but nothing on the board uses it")
    return errors, warnings


def compose(spec: dict) -> Design:
    """Validated spec -> Design with resolved footprints (not yet placed)."""
    clean, errors, warnings = validate_spec(spec)
    if errors:
        raise SpecError(errors)
    volts = net_voltages(clean)
    design = Design(title=clean["title"], warnings=list(warnings))
    counters: dict[str, int] = defaultdict(int)
    nets: set[str] = set()
    for binst in clean["blocks"]:
        block = BLOCKS[binst["type"]]
        ctx = Ctx(binst["id"], voltages=volts)
        try:
            parts = block.build(ctx, binst["nets"], binst["params"])
        except BlockParamError as exc:
            raise SpecError([f"block {binst['id']!r}: {exc}"]) from exc
        refs_in_block = []
        for ps in parts:
            counters[ps.prefix] += 1
            ref = f"{ps.prefix}{counters[ps.prefix]}"
            refs_in_block.append(ref)
            fp, used, last_exc = None, ps.lib_id, None
            for cand in (ps.lib_id, *ps.alt):
                try:
                    fp, used = resolve(cand), cand
                    break
                except FootprintError as exc:
                    last_exc = exc
            if fp is None:
                tried = f" (also tried: {', '.join(ps.alt)})" if ps.alt else ""
                raise SpecError([f"block {binst['id']!r}: {last_exc}{tried}"]) from last_exc
            have = {p.number for p in fp.pads}
            missing = [n for n in ps.pins if n not in have]
            if missing:
                raise SpecError([f"block {binst['id']!r}: footprint {ps.lib_id} has no pad(s) "
                                 f"{missing} - the block's pin map does not match this footprint"])
            near = None
            if ps.near:
                parent_idx, side, along = ps.near
                near = (refs_in_block[parent_idx], side, along)
            design.components.append(Component(
                ref=ref, value=ps.value, lib_id=used, block_id=binst["id"],
                pin_nets=dict(ps.pins), x=ps.x, y=ps.y, rot=ps.rot, mpn=ps.mpn,
                review=ps.review or block.review_required, fp=fp, near=near))
            nets.update(ps.pins.values())
        if block.currents:
            for net, amps in block.currents(ctx, binst["nets"], binst["params"]).items():
                design.net_current[net] = max(design.net_current.get(net, 0.0), float(amps))
        if block.review_required:
            design.review_items.append(f"{block.title}: {block.notes}")
    design.nets = sorted(nets)
    for net, amps in design.net_current.items():
        w = width_for_current(amps)
        if net in nets and w > RULES["track_width"] + 1e-9:
            design.net_width[net] = w
    counts = defaultdict(int)
    for c in design.components:
        for n in c.pin_nets.values():
            counts[n] += 1
    for n, k in counts.items():
        if k < 2:
            design.warnings.append(f"net {n} has only one connection")
    design._max_width = clean["board"]["max_width_mm"]  # type: ignore[attr-defined]
    design._spec = clean  # type: ignore[attr-defined]
    return design
