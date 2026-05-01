#!/usr/bin/env python3

from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import netCDF4
import numpy as np
import pandas as pd
from netCDF4 import Dataset


STATUS_HEADER = (
    "#StatusFlag: 0(Used);1(Not Used);2(Rejected by DA);"
    "3(Never Used by DA);4(Data Thinned);5(Rejected before DA);"
    "6(Alternative Used);7(Quality Issue);8(Other Reason);9(No content)"
)

CYCLE_WINDOWS = {
    "00": ("21", "03"),
    "06": ("03", "09"),
    "12": ("09", "15"),
    "18": ("15", "21"),
}


@dataclass(frozen=True)
class WdqmsConfig:
    obs_types: tuple[int, ...]
    variable_ids: dict[str, int]
    kind: str


WDQMS_CONFIG = {
    "SYNOP": WdqmsConfig(
        obs_types=(181, 187, 281, 287),
        variable_ids={"ps": 110, "q": 58, "t": 39, "u": 41, "v": 42},
        kind="surface",
    ),
    "MARINE": WdqmsConfig(
        obs_types=(180, 183, 280, 282, 284),
        variable_ids={"ps": 110, "q": 58, "t": 39, "u": 41, "v": 42},
        kind="surface",
    ),
    "TEMP": WdqmsConfig(
        obs_types=(120, 220),
        variable_ids={"ps": 110, "q": 29, "t": 2, "u": 3, "v": 4},
        kind="upper_air",
    ),
}


SURFACE_COLUMNS = [
    "Station_id", "yyyymmdd", "HHMMSS", "latitude", "Longitude",
    "StatusFlag", "Centre_id", "var_id", "Bg_dep", "CodeType",
    "Wigos_Id", "Timeliness",
]

TEMP_COLUMNS = [
    "Station_id", "yyyymmdd", "HHMMSS", "latitude", "Longitude",
    "StatusFlag", "Centre_id", "var_id", "Mean_Bg_dep", "Std_Bg_dep",
    "Levels", "LastRepLevel", "CodeType", "Wigos_Id", "Timeliness",
]


class WDQMS:
    def __init__(
        self,
        inputfiles: list[str | Path],
        wdqms_type: str,
        outdir: str | Path,
        centre_id: str = "NCEP",
        loglevel: int = logging.INFO,
    ):
        self.inputfiles = [Path(f) for f in inputfiles]
        self.wdqms_type = wdqms_type.upper()
        self.config = WDQMS_CONFIG[self.wdqms_type]
        self.outdir = Path(outdir)
        self.centre_id = centre_id

        logging.basicConfig(
            filename="wdqms.log",
            filemode="w",
            level=loglevel,
            format="%(levelname)s:%(message)s",
        )

    def run(self) -> Path:
        self.outdir.mkdir(parents=True, exist_ok=True)

        df = pd.concat(
            (self.read_gsi_diag(path) for path in self.inputfiles),
            ignore_index=True,
            copy=False,
        )

        df = self.filter_wdqms_type(df)
        df = self.add_observation_datetime(df)
        df = self.filter_to_cycle_window(df)
        df = self.add_required_missing_fields(df)
        df = self.add_status_flag(df)

        # Only do the expensive q->RH conversion when q and t are both present.
        var_ids = set(df["var_id"].unique())
        if (
            self.config.variable_ids["q"] in var_ids
            and self.config.variable_ids["t"] in var_ids
        ):
            df = self.add_relative_humidity_departures_fast(df)

        df = self.drop_exact_duplicates(df)

        if self.config.kind == "upper_air":
            out = self.create_temp_output_fast(df)
        else:
            out = self.create_surface_output(df)

        date, cycle = self.analysis_date_cycle()
        return self.write_wdqms_csv(out, date, cycle)

    def analysis_date_cycle(self) -> tuple[str, str]:
        match = re.search(r"(\d{10})", self.inputfiles[0].name)
        if not match:
            raise ValueError(f"Could not find YYYYMMDDHH in filename: {self.inputfiles[0]}")
        ymdh = match.group(1)
        return ymdh[:8], ymdh[8:10]

    def read_gsi_diag(self, path: Path) -> pd.DataFrame:
        var = self.variable_from_filename(path)

        if var == "uv":
            return pd.concat(
                [self.read_single_variable(path, "u"), self.read_single_variable(path, "v")],
                ignore_index=True,
                copy=False,
            )

        return self.read_single_variable(path, var)

    @staticmethod
    def variable_from_filename(path: Path) -> str:
        name = path.name
        for var in ("uv", "ps", "q", "t"):
            if f"conv_{var}_" in name or f"_{var}_" in name:
                return var
        raise ValueError(f"Could not infer variable from filename: {path}")

    def read_single_variable(self, path: Path, variable: str) -> pd.DataFrame:
        var_id = self.config.variable_ids[variable]

        base_vars = [
            "Station_ID",
            "Observation_Type",
            "Observation_Subtype",
            "Latitude",
            "Longitude",
            "Pressure",
            "Time",
            "Prep_QC_Mark",
            "Prep_Use_Flag",
            "Analysis_Use_Flag",
        ]

        with Dataset(path) as nc:
            data = {}
            for name in base_vars:
                if name in nc.variables:
                    data[name] = self.read_nc_var(nc, name)

            data["Datetime"] = getattr(nc, "date_time")

            if variable in {"u", "v"}:
                obs_name = f"{variable}_Observation"
                omf_name = f"{variable}_Obs_Minus_Forecast_adjusted"

                if obs_name not in nc.variables:
                    obs_name = "Observation"
                if omf_name not in nc.variables:
                    omf_name = "Obs_Minus_Forecast_adjusted"

                data["Observation"] = self.read_nc_var(nc, obs_name)
                data["Obs_Minus_Forecast_adjusted"] = self.read_nc_var(nc, omf_name)
            else:
                data["Observation"] = self.read_nc_var(nc, "Observation")
                data["Obs_Minus_Forecast_adjusted"] = self.read_nc_var(
                    nc, "Obs_Minus_Forecast_adjusted"
                )

        df = pd.DataFrame(data)
        df["var_id"] = var_id

        if "Longitude" in df:
            df["Longitude"] = np.where(
                df["Longitude"] > 180.0,
                df["Longitude"] - 360.0,
                df["Longitude"],
            )

        return df

    @staticmethod
    def read_nc_var(nc: Dataset, name: str):
        arr = nc.variables[name][:]

        if name == "Station_ID":
            try:
                return netCDF4.chartostring(arr).astype(str)
            except Exception:
                return np.array([
                    "".join(row.astype(str)).strip()
                    for row in np.asarray(arr)
                ])

        return np.asarray(arr)

    def filter_wdqms_type(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.loc[df["Observation_Type"].isin(self.config.obs_types)].copy()

        if self.wdqms_type in {"SYNOP", "MARINE"}:
            bad_ps = (
                (df["var_id"] == 110)
                & (df["Obs_Minus_Forecast_adjusted"].abs() > 200)
            )
            bad_other = (
                (df["var_id"] != 110)
                & (df["Obs_Minus_Forecast_adjusted"].abs() > 500)
            )

            df.loc[bad_ps | bad_other, "Obs_Minus_Forecast_adjusted"] = 9999.9

        return df

    @staticmethod
    def add_observation_datetime(df: pd.DataFrame) -> pd.DataFrame:
        base = pd.to_datetime(df["Datetime"].astype(str), format="%Y%m%d%H")
        obs_dt = base + pd.to_timedelta(df["Time"], unit="h")

        df = df.copy()
        df["obs_datetime"] = obs_dt
        df["yyyymmdd"] = obs_dt.dt.strftime("%Y%m%d")
        df["HHMMSS"] = obs_dt.dt.strftime("%H%M%S")
        return df

    @staticmethod
    def filter_to_cycle_window(df: pd.DataFrame) -> pd.DataFrame:
        # WDQMS 6-hour files are centered on synoptic time.
        return df.loc[(df["Time"] >= -3.0) & (df["Time"] < 3.0)].copy()

    @staticmethod
    def add_required_missing_fields(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        if "Wigos_Id" not in df.columns:
            df["Wigos_Id"] = "99999"

        if "Timeliness" not in df.columns:
            df["Timeliness"] = -9999

        return df

    @staticmethod
    def add_status_flag(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        conditions = [
            (df["Prep_QC_Mark"] <= 8) & (df["Analysis_Use_Flag"] == 1),
            (df["Prep_QC_Mark"] <= 8) & (df["Analysis_Use_Flag"] == -1),
            (df["Prep_QC_Mark"] > 8) & (df["Prep_Use_Flag"] >= 100),
            df["Prep_QC_Mark"] >= 15,
            df["Prep_QC_Mark"].between(12, 14),
        ]

        choices = [
            0,  # Used
            2,  # Rejected by DA
            3,  # Never Used by DA
            3,  # Never Used by DA / non-use
            7,  # Quality Issue
        ]

        df["StatusFlag"] = np.select(conditions, choices, default=1).astype(np.int16)
        return df

    def add_relative_humidity_departures_fast(self, df: pd.DataFrame) -> pd.DataFrame:
        q_id = self.config.variable_ids["q"]
        t_id = self.config.variable_ids["t"]

        keys = ["Station_ID", "Latitude", "Longitude", "Pressure", "Time", "Datetime"]

        q = df.loc[df["var_id"] == q_id].drop_duplicates(subset=keys)
        t = df.loc[df["var_id"] == t_id].drop_duplicates(subset=keys)

        if q.empty or t.empty:
            return df

        t_small = t[keys + ["Observation", "Obs_Minus_Forecast_adjusted"]].rename(
            columns={
                "Observation": "Observation_t",
                "Obs_Minus_Forecast_adjusted": "Obs_Minus_Forecast_adjusted_t",
            }
        )

        merged = q.merge(t_small, on=keys, how="left", sort=False)
        matched = merged["Observation_t"].notna()

        if not matched.any():
            return df

        q_obs = merged.loc[matched, "Observation"].astype(float)
        q_ges = q_obs - merged.loc[matched, "Obs_Minus_Forecast_adjusted"].astype(float)

        t_obs = merged.loc[matched, "Observation_t"].astype(float)
        t_ges = t_obs - merged.loc[matched, "Obs_Minus_Forecast_adjusted_t"].astype(float)

        p_hpa = merged.loc[matched, "Pressure"].astype(float)

        rh_obs = q_obs / self.qsat(t_obs, p_hpa)
        rh_ges = q_ges / self.qsat(t_ges, p_hpa)

        updated_q = merged.loc[matched, df.columns].copy()
        updated_q["Observation"] = rh_obs.to_numpy()
        updated_q["Obs_Minus_Forecast_adjusted"] = (rh_obs - rh_ges).to_numpy()

        unmatched_q = merged.loc[~matched, df.columns]
        non_q = df.loc[df["var_id"] != q_id]

        return pd.concat([non_q, updated_q, unmatched_q], ignore_index=True, copy=False)

    @staticmethod
    def qsat(t_k: pd.Series, p_hpa: pd.Series) -> pd.Series:
        t_c = t_k - 273.15
        es_hpa = 6.112 * np.exp((17.67 * t_c) / (t_c + 243.5))
        qsat_kgkg = 0.622 * es_hpa / (p_hpa - 0.378 * es_hpa)
        return qsat_kgkg * 1000.0

    @staticmethod
    def drop_exact_duplicates(df: pd.DataFrame) -> pd.DataFrame:
        subset = [
            "Station_ID", "yyyymmdd", "HHMMSS", "Latitude", "Longitude",
            "Pressure", "var_id", "Observation_Type",
        ]
        subset = [c for c in subset if c in df.columns]
        return df.drop_duplicates(subset=subset)

    def create_surface_output(self, df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame({
            "Station_id": df["Station_ID"].astype(str),
            "yyyymmdd": df["yyyymmdd"],
            "HHMMSS": df["HHMMSS"],
            "latitude": df["Latitude"],
            "Longitude": df["Longitude"],
            "StatusFlag": df["StatusFlag"].astype(int),
            "Centre_id": self.centre_id,
            "var_id": df["var_id"].astype(int),
            "Bg_dep": df["Obs_Minus_Forecast_adjusted"],
            "CodeType": df["Observation_Type"].astype(int),
            "Wigos_Id": df["Wigos_Id"].astype(str),
            "Timeliness": df["Timeliness"].astype(int),
        })

        return (
            out[SURFACE_COLUMNS]
            .sort_values(["Station_id", "yyyymmdd", "HHMMSS", "var_id"], kind="mergesort")
            .reset_index(drop=True)
        )

    def create_temp_output_fast(self, df: pd.DataFrame) -> pd.DataFrame:
        ps_id = self.config.variable_ids["ps"]

        group_base = ["Station_ID", "yyyymmdd", "HHMMSS"]

        # Use profile-level representative metadata.
        profile_meta = (
            df.sort_values(group_base + ["Pressure"], ascending=[True, True, True, False])
            .groupby(group_base, sort=False)
            .agg(
                latitude=("Latitude", "first"),
                Longitude=("Longitude", "first"),
                LastRepLevel=("Pressure", "min"),
                CodeType=("Observation_Type", "first"),
                Wigos_Id=("Wigos_Id", "first"),
                Timeliness=("Timeliness", "first"),
            )
            .reset_index()
        )

        # Trop/Stra rows for non-surface-pressure variables.
        prof = df.loc[df["var_id"] != ps_id].copy()
        prof["Levels"] = np.where(prof["Pressure"] < 100.0, "Stra", "Trop")

        layer = (
            prof.groupby(group_base + ["var_id", "Levels"], sort=False)
            .agg(
                Mean_Bg_dep=("Obs_Minus_Forecast_adjusted", "mean"),
                Std_Bg_dep=("Obs_Minus_Forecast_adjusted", "std"),
                StatusFlag=("StatusFlag", "min"),
            )
            .reset_index()
        )
        layer["Std_Bg_dep"] = layer["Std_Bg_dep"].fillna(0.0)

        layer = layer.merge(profile_meta, on=group_base, how="left", sort=False)
        layer["Centre_id"] = self.centre_id

        # Optional surface-pressure row.
        ps = df.loc[df["var_id"] == ps_id]
        if not ps.empty:
            ps_out = (
                ps.groupby(group_base, sort=False)
                .agg(
                    Mean_Bg_dep=("Obs_Minus_Forecast_adjusted", "first"),
                    StatusFlag=("StatusFlag", "min"),
                    latitude=("Latitude", "first"),
                    Longitude=("Longitude", "first"),
                    CodeType=("Observation_Type", "first"),
                    Wigos_Id=("Wigos_Id", "first"),
                    Timeliness=("Timeliness", "first"),
                )
                .reset_index()
            )

            ps_out["var_id"] = ps_id
            ps_out["Std_Bg_dep"] = 0.0
            ps_out["Levels"] = "Surf"
            ps_out["LastRepLevel"] = -999.99
            ps_out["Centre_id"] = self.centre_id

            out = pd.concat([layer, ps_out], ignore_index=True, copy=False)
        else:
            out = layer

        out = out.rename(columns={"Station_ID": "Station_id"})
        out = out.reindex(columns=TEMP_COLUMNS)

        return (
            out.sort_values(
                ["Station_id", "yyyymmdd", "HHMMSS", "var_id", "Levels"],
                kind="mergesort",
            )
            .reset_index(drop=True)
        )

    def write_wdqms_csv(self, df: pd.DataFrame, date: str, cycle: str) -> Path:
        start, end = CYCLE_WINDOWS.get(cycle, ("-3", "+3"))
        outfile = self.outdir / f"{self.centre_id}_{self.wdqms_type}_{date}_{cycle}.csv"

        with outfile.open("w", newline="") as f:
            f.write(f"# TYPE={self.wdqms_type}\n")
            f.write(f"#An_date= {date}\n")
            f.write(f"#An_time= {cycle}\n")
            f.write(f"#An_range=] {start} to {end} ]\n")
            f.write(STATUS_HEADER + "\n")
            f.write("#" + ",".join(df.columns) + "\n")

            df.to_csv(
                f,
                index=False,
                header=False,
                float_format="%.4f",
            )

        return outfile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create WDQMS-compliant SYNOP, MARINE, or TEMP monitoring CSV files."
    )
    parser.add_argument(
        "-i", "--input-list",
        nargs="+",
        required=True,
        help="Input GSI diag nc4 files.",
    )
    parser.add_argument(
        "-t", "--type",
        choices=WDQMS_CONFIG.keys(),
        required=True,
        help="WDQMS output type.",
    )
    parser.add_argument(
        "-o", "--outdir",
        required=True,
        help="Output directory.",
    )
    parser.add_argument(
        "--centre-id",
        default="NCEP",
        help="WDQMS centre identifier.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_const",
        dest="loglevel",
        const=logging.INFO,
        default=logging.WARNING,
    )
    parser.add_argument(
        "-d", "--debug",
        action="store_const",
        dest="loglevel",
        const=logging.DEBUG,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    outfile = WDQMS(
        inputfiles=args.input_list,
        wdqms_type=args.type,
        outdir=args.outdir,
        centre_id=args.centre_id,
        loglevel=args.loglevel,
    ).run()

    print(outfile)


if __name__ == "__main__":
