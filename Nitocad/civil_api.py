"""
HTTP routes for the civil calculations in civil/. Included from web_app.py with

    app.include_router(civil_api.build_router(limiter=limiter))

Signed-in accounts only, rate limited, and no credit is charged. Every route is a
stateless calculation: nothing is stored, and the survey text is parsed in memory
and dropped. Heavy work (a surface from thousands of points, an earthworks run)
runs in a worker thread so it does not block other requests.

  GET  /civil/criteria/default        the generic starter criteria (editable)
  POST /civil/terrain                 survey text -> surface statistics (+ STL)
  POST /civil/alignment               PI and PVI lists -> stations, curves, checks
  POST /civil/earthworks              survey + alignment + road template -> volumes
  POST /civil/hydrology/rational      rational method peak flow
  POST /civil/hydraulics/pipe         pipe capacity, size selection, checks
  POST /civil/hydraulics/channel      trapezoidal channel depth, velocity, checks
  POST /civil/hydraulics/culvert      box culvert screening check
  POST /civil/loads/culvert           pressures on a buried box culvert
  POST /civil/wall/stability          cantilever wall overturning, sliding, bearing
"""

from dataclasses import asdict
from typing import Annotated, Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import wallet_auth
from civil import alignment as al
from civil import criteria as cr
from civil import earthworks as ew
from civil import hydraulics as hy
from civil import loads, survey, terrain, wall_stability
from civil.errors import CivilInputError
from config import settings

Wallet = Annotated[dict, Depends(wallet_auth.get_current_wallet)]

MAX_SURVEY_CHARS = 400_000
Point2 = tuple[float, float]
Pvi = tuple[float, float, float]


class SurveyIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_SURVEY_CHARS)
    units: Literal["m", "ft"] = "m"
    max_edge_m: float | None = Field(default=None, gt=0)


class TerrainBody(SurveyIn):
    include_stl: bool = False
    z_exaggeration: float = Field(default=1.0, gt=0, le=50)


class AlignmentIn(BaseModel):
    pis: list[Point2] = Field(min_length=2, max_length=500)
    radii: list[float] | None = Field(default=None, max_length=500)
    pvis: list[Pvi] = Field(min_length=2, max_length=500)


class AlignmentBody(AlignmentIn):
    station_interval_m: float = Field(default=20.0, gt=0)
    lane_width_m: float = 3.5
    crossfall_pct: float = 2.5
    criteria: dict[str, Any] | None = None


class TemplateIn(BaseModel):
    lane_width_m: float = 3.5
    lane_count: int = 2
    shoulder_width_m: float = 1.5
    crossfall_pct: float = 2.5
    shoulder_crossfall_pct: float = 4.0
    pavement_thickness_m: float = 0.3
    cut_slope: float = 2.0
    fill_slope: float = 2.0
    has_kerbs: bool = False
    pavement_layers: list[tuple[str, float]] = Field(default_factory=list, max_length=12)


class EarthworksBody(BaseModel):
    survey: SurveyIn
    alignment: AlignmentIn
    template: TemplateIn = Field(default_factory=TemplateIn)
    station_interval_m: float = Field(default=20.0, gt=0)
    section_step_m: float = 0.5
    max_slope_reach_m: float = 25.0
    fill_factor: float = 1.0
    topsoil_strip_depth_m: float = 0.0
    criteria: dict[str, Any] | None = None


class RationalBody(BaseModel):
    runoff_coeff: float
    area_ha: float
    intensity_mm_h: float | None = None
    idf: tuple[float, float, float] | None = None      # a, b, c
    flow_length_m: float | None = None
    slope_m_per_m: float | None = None
    duration_min: float | None = None


class PipeBody(BaseModel):
    flow_m3s: float
    slope: float
    n: float = 0.013
    diameter_mm: float | None = None
    max_fill_ratio: float = 0.8
    criteria: dict[str, Any] | None = None


class ChannelBody(BaseModel):
    flow_m3s: float
    bottom_width_m: float
    side_slope: float = 2.0
    slope: float
    n: float = 0.025
    criteria: dict[str, Any] | None = None


class CulvertBody(BaseModel):
    flow_m3s: float
    clear_width_m: float
    clear_height_m: float
    length_m: float
    slope: float
    n: float = 0.013
    barrels: int = 1
    weir_coeff: float = 1.5
    orifice_coeff: float = 0.6
    criteria: dict[str, Any] | None = None


class CulvertLoadsBody(BaseModel):
    clear_width_m: float
    clear_height_m: float
    wall_thickness_m: float
    slab_thickness_m: float
    barrels: int = 1
    cover_height_m: float = 1.0
    soil_unit_weight: float = 18.0
    soil_phi_deg: float = 30.0
    live_surcharge_kpa: float = 0.0


class WallBody(BaseModel):
    stem_height_m: float
    stem_thickness_base_m: float
    stem_thickness_top_m: float
    base_width_m: float
    base_thickness_m: float
    toe_length_m: float
    backfill_unit_weight: float = 18.0
    backfill_phi_deg: float = 30.0
    foundation_phi_deg: float = 30.0
    base_adhesion_kpa: float = 0.0
    concrete_unit_weight: float = loads.CONCRETE_UNIT_WEIGHT
    surcharge_kpa: float = 0.0
    include_passive: bool = False
    embedment_m: float = 0.0
    allowable_bearing_kpa: float | None = None
    criteria: dict[str, Any] | None = None


def _build_alignment(a: AlignmentIn) -> tuple[al.HorizontalAlignment, al.VerticalAlignment]:
    return al.HorizontalAlignment(list(a.pis), a.radii), al.VerticalAlignment(list(a.pvis))


def _terrain(s: SurveyIn):
    res = survey.parse_points(s.text, units=s.units, max_points=settings.CIVIL_MAX_SURVEY_POINTS)
    t = terrain.Terrain([(p.x, p.y, p.z) for p in res.points], max_edge_m=s.max_edge_m)
    return res, t


def _checked(criteria_dict, findings_fn: Callable[[cr.Criteria], list[dict]]) -> dict:
    c = cr.criteria_from_dict(criteria_dict)
    return cr.summarize(findings_fn(c), c)


def build_router(*, limiter) -> APIRouter:
    router = APIRouter(prefix="/civil", tags=["civil"])
    rate = settings.RATE_LIMIT_CIVIL

    async def run(fn: Callable[[], dict]) -> dict:
        try:
            return await run_in_threadpool(fn)
        except CivilInputError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

    @router.get("/criteria/default")
    @limiter.limit(rate)
    async def default_criteria(request: Request, wallet: Wallet):
        c = cr.generic_starter()
        return {"criteria": asdict(c), "min_curve_radius_from_speed_m": cr.min_radius_from_speed(c),
                "note": "Generic starting values, not a design standard. Replace them with your governing standard's limits."}

    @router.post("/terrain")
    @limiter.limit(rate)
    async def terrain_route(request: Request, body: TerrainBody, wallet: Wallet):
        def work():
            res, t = _terrain(body)
            out = {"survey": {"stats": res.stats, "warnings": res.warnings}, "terrain": t.stats()}
            if body.include_stl:
                out["stl"] = t.to_ascii_stl(z_exaggeration=body.z_exaggeration)
            return out
        return await run(work)

    @router.post("/alignment")
    @limiter.limit(rate)
    async def alignment_route(request: Request, body: AlignmentBody, wallet: Wallet):
        def work():
            h, v = _build_alignment(body)
            if v.end + 1e-6 < h.length or v.start > 1e-6:
                raise CivilInputError(
                    f"The vertical alignment ({v.start:.1f} to {v.end:.1f} m) must cover the horizontal one (0 to {h.length:.1f} m)")
            stations = []
            for s in al.stations_for(h.length, body.station_interval_m, h.key_stations()):
                p = h.point_at(s)
                stations.append({"station": s, "x": p["x"], "y": p["y"], "bearing_deg": p["bearing_deg"],
                                 "design_z": v.elevation(s), "grade_pct": v.grade_at(s) * 100.0})
            checks = _checked(body.criteria, lambda c: cr.check_road(
                c, lane_width_m=body.lane_width_m, crossfall_pct=body.crossfall_pct, horizontal=h, vertical=v))
            return {"length_m": h.length, "horizontal_curves": h.curves, "vertical_curves": v.curves,
                    "max_grade_pct": v.max_abs_grade_pct(), "stations": stations, "checks": checks}
        return await run(work)

    @router.post("/earthworks")
    @limiter.limit(rate)
    async def earthworks_route(request: Request, body: EarthworksBody, wallet: Wallet):
        def work():
            res, t = _terrain(body.survey)
            h, v = _build_alignment(body.alignment)
            tpl = ew.RoadTemplate(**{**body.template.model_dump(),
                                     "pavement_layers": tuple(tuple(x) for x in body.template.pavement_layers)})
            out = ew.compute_earthworks(
                t, h, v, tpl, station_interval_m=body.station_interval_m, section_step_m=body.section_step_m,
                max_slope_reach_m=body.max_slope_reach_m, fill_factor=body.fill_factor,
                topsoil_strip_depth_m=body.topsoil_strip_depth_m)
            out["survey"] = {"stats": res.stats, "warnings": res.warnings}
            out["checks"] = _checked(body.criteria, lambda c: cr.check_road(
                c, lane_width_m=tpl.lane_width_m, crossfall_pct=tpl.crossfall_pct, horizontal=h, vertical=v))
            return out
        return await run(work)

    @router.post("/hydrology/rational")
    @limiter.limit(rate)
    async def rational_route(request: Request, body: RationalBody, wallet: Wallet):
        def work():
            tc = None
            if body.flow_length_m is not None and body.slope_m_per_m is not None:
                tc = hy.kirpich_tc_min(body.flow_length_m, body.slope_m_per_m)
            i = body.intensity_mm_h
            source = "supplied"
            if i is None:
                if body.idf is None:
                    raise CivilInputError("Give intensity_mm_h, or idf coefficients (a, b, c) with a duration or a flow length and slope")
                dur = body.duration_min if body.duration_min is not None else tc
                if dur is None:
                    raise CivilInputError("Give duration_min, or flow_length_m and slope_m_per_m so the time of concentration can be found")
                i = hy.idf_intensity_mm_h(*body.idf, dur)
                source = f"IDF curve at {dur:.1f} min"
            q = hy.rational_flow(body.runoff_coeff, i, body.area_ha)
            return {"flow_m3s": q, "intensity_mm_h": i, "intensity_source": source,
                    "time_of_concentration_min": tc, "runoff_coeff": body.runoff_coeff, "area_ha": body.area_ha,
                    "notes": ["No rainfall data is built in. Use intensities from your site's records or design tables."]}
        return await run(work)

    @router.post("/hydraulics/pipe")
    @limiter.limit(rate)
    async def pipe_route(request: Request, body: PipeBody, wallet: Wallet):
        def work():
            if body.diameter_mm is not None:
                r = hy.pipe_flow(body.diameter_mm / 1000.0, body.slope, body.n, body.flow_m3s)
                r["selected_diameter_mm"] = body.diameter_mm
            else:
                r = hy.select_pipe(body.flow_m3s, body.slope, body.n, body.max_fill_ratio)
            r["checks"] = _checked(body.criteria, lambda c: cr.check_pipe(c, r))
            return r
        return await run(work)

    @router.post("/hydraulics/channel")
    @limiter.limit(rate)
    async def channel_route(request: Request, body: ChannelBody, wallet: Wallet):
        def work():
            r = hy.trapezoid_normal_depth(body.flow_m3s, body.bottom_width_m, body.side_slope, body.slope, body.n)
            r["checks"] = _checked(body.criteria, lambda c: cr.check_channel(c, r))
            return r
        return await run(work)

    @router.post("/hydraulics/culvert")
    @limiter.limit(rate)
    async def culvert_route(request: Request, body: CulvertBody, wallet: Wallet):
        def work():
            r = hy.box_culvert_check(body.flow_m3s, body.clear_width_m, body.clear_height_m, body.length_m,
                                     body.slope, body.n, body.barrels, body.weir_coeff, body.orifice_coeff)
            r["checks"] = _checked(body.criteria, lambda c: cr.check_culvert(c, r))
            return r
        return await run(work)

    @router.post("/loads/culvert")
    @limiter.limit(rate)
    async def culvert_loads_route(request: Request, body: CulvertLoadsBody, wallet: Wallet):
        return await run(lambda: loads.culvert_loads(**body.model_dump()))

    @router.post("/wall/stability")
    @limiter.limit(rate)
    async def wall_route(request: Request, body: WallBody, wallet: Wallet):
        def work():
            args = body.model_dump()
            criteria_dict = args.pop("criteria")
            r = wall_stability.check_wall(**args)
            r["checks"] = _checked(criteria_dict, lambda c: cr.check_wall(c, r))
            return r
        return await run(work)

    return router
