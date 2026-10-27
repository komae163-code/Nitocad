# Geometry benchmark

`eval_geometry.py` answers one question: when someone types a prompt, how often do they get the right solid, how fast, and at what cost.

It is separate from `eval_parser.py`. The parser eval checks that a description is read correctly. This one builds the part with CadQuery and measures the result, so it also catches a correct parse that builds the wrong thing, and a part that fails to build at all.

## Run it

Run it where the app runs, because it needs CadQuery.

    python eval_geometry.py                                    # rule-based parser, free
    python eval_geometry.py --mode llm --price-in 0.30 --price-out 1.20
    python eval_geometry.py --label v17 --out bench-v17.json --markdown docs/BENCHMARK_RESULTS.md
    python eval_geometry.py --baseline bench-v16.json --max-regression 0.02

`--price-in` and `--price-out` are dollars per million tokens. Check them against your provider's current price list. Without them, tokens are reported and dollars are left blank.

Useful flags: `--only flange` runs one part type, `--limit 20` runs a quick slice, `--verbose` prints each miss with the reason, `--fail-under-completion` and `--fail-under-correct` set exit codes for CI.

## What the numbers mean

| Number | Question it answers |
|---|---|
| Completion | Did the prompt give a valid, single, non-empty solid? Wrong size still counts. |
| Correct | Did it complete AND match the expected size, volume and, where the prompt states them, hole count? |
| Seconds per part | Wall time for parse, validate, build, verify and export, with the kernel warmed up first. |
| Cost per part | Tokens times your prices when an AI parser ran. The rule-based parser is zero. |

Completion is the headline claim and Correct is the honest one. Report both.

## The cases

`tests/eval/geometry_cases.json` holds 249 prompts across plates, shafts, tubes, spacers, washers, bearings, spheres, flanges, boxes, gears, pulleys, sprockets, L-brackets and angles, with some in inches and centimetres. Each carries the size, volume and hole count of the part the prompt describes.

The expected numbers come from formulas in `tests/eval/make_geometry_cases.py`, so nothing is typed in by hand. To change a case, edit that file and run it:

    python tests/eval/make_geometry_cases.py

A test fails if the JSON and the generator disagree.

The cases are held out. None is a template picker prompt and none is in `tests/eval/parser_cases.json`. Tune the parser on that set and keep this one for scoring. If you tune against these cases, the result stops meaning anything, so add new cases for new work instead.

A case means what a person means, not what the template defaults to. "No holes" expects no holes, and "6 holes" expects six. A part built with holes nobody asked for is a miss.

## Looser or design-based cases

- **Gears** are held to the area that gear theory gives, within 3%: the teeth fill about half the ring between the root and tip circles. That catches a gear with missing teeth, no teeth, or a plain disc. Tooth counts are a multiple of four so the bounding box is exactly the tip circle.
- **Sprockets** are held to the template's own simplified tooth space (a circle of a third of the pitch on the pitch circle). That checks every tooth space is there, and it does not check the sprocket against a real chain. Size and hole count are not scored for them, because the tooth spaces are round cuts and the hole counter would count each one.

## What it does not cover

- Prompts with a fillet, chamfer or keyway are left out, since the volume then depends on the fillet.
- Hinges, cams, T-brackets, channel brackets and pipe fittings have no case. See `docs/TEMPLATE_AUDIT.md` for why.
- Assemblies are not scored here. The gate in `assembly_gate.py` covers the plan and `assembly_checks.py` covers the built solids.
- Holes are counted on the STEP file as round inward-facing faces grouped by axis. If a part's STEP cannot be read, its hole count is left unscored, and the summary shows how many were scored.

## In CI

The `benchmark` job in `.github/workflows/ci.yml` runs this on the default branch after the tests pass, with the rule-based parser. It never fails the run. Its table appears on the run's summary page and the full results are saved as the `geometry-benchmark` artifact. Run `--mode llm` by hand when you want the AI parser's numbers, since it needs a key and costs money.

## Reading the first run

The first run of a new case set will include misses caused by the product and misses caused by a wrong expectation. Read the `--verbose` output before publishing anything. A miss where the part is right and the case is wrong is a bug in `make_geometry_cases.py`. Fix the formula, regenerate, and rerun.

Misses that are real findings are common on the rule-based parser. For example, it reads "6 holes" on a plate as 4 across and 1 down, and has no way to say "no holes".
