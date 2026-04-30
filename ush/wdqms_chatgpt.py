#!/usr/bin/env python3

from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from netCDF4 import Dataset


STATUS_TEXT = (
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
    output_kind: str


WDQMS_CONFIG = {
    "SYNOP": WdqmsConfig(
        obs_types=(181, 187, 281, 287),
        variable_ids={"ps": 110, "q": 58, "t": 39, "u": 41, "v": 42},
        output_kind="surface",
    ),
    "MARINE": WdqmsConfig(
        obs_types=(180, 183, 280, 282, 284),
        variable_ids={"ps": 110, "q": 58, "t": 39, "u": 41, "v": 42},
        output_kind="surface",
    ),
    "TEMP": WdqmsConfig(
        obs_types=(120, 220),
        variable_ids={"ps": 110, "q": 29, "t": 2, "u": 3, "v": 4},
        output_kind="upper_air",
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
            [self.read_gsi_diag(path) for path in self.inputfiles],
            ignore_index=True,
        )

        df = (
            df.pipe(self.filter_wdqms_type)
              .pipe(self.add_observation_datetime)
              .pipe(self.filter_to_cycle_window)
              .pipe(self.add_relative_humidity_departures)
              .pipe(self.add_status_flag)
              .pipe(self.add_required_missing_fields)
              .drop_duplicates()
        )

        if self.config.output_kind == "upper_air":
            output = self.create_temp_output(df)
        else:
            output = self.create_surface_output(df)

        date, cycle = self.analysis_date_cycle()
        return self.write_wdqms_csv(output, date, cycle)

    def analysis_date_cycle(self) -> tuple[str, str]:
        """
        Extract YYYYMMDDHH from the first diag filename, e.g.
        diag_conv_t_ges.2026042800.nc4.
        """
        match = re.search(r"(\d{10})", self.inputfiles[0].name)
        if not match:
            raise ValueError(f"Could not find YYYYMMDDHH in {self.inputfiles[0]}")
        ymdh = match.group(1)
        return ymdh[:8], ymdh[8:10]

    def read_gsi_diag(self, path: Path) -> pd.DataFrame:
        """
        Read one GSI conventional diagnostic file into long-form WDQMS rows.
        """
        variable = self.variable_from_filename(path)

        if variable == "uv":
            u = self.read_single_variable(path, "u")
            v = self.read_single_variable(path, "v")
            return pd.concat([u, v], ignore_index=True)

        return self.read_single_variable(path, variable)

    def variable_from_filename(self, path: Path) -> str:
        """
        Expected examples:
          diag_conv_t_ges.YYYYMMDDHH.nc4
          diag_conv_q_ges.YYYYMMDDHH.nc4
          diag_conv_uv_ges.YYYYMMDDHH.nc4
          diag_conv_ps_ges.YYYYMMDDHH.nc4
        """
        name = path.name
        for var in ("uv", "ps", "q", "t"):
            if f"conv_{var}_" in name or f"_{var}_" in name:
                return var
        raise ValueError(f"Could not infer variable from filename: {path}")

    def read_single_variable(self, path: Path, variable: str) -> pd.DataFrame:
        var_id = self.config.variable_ids[variable]

        base_cols = [
            "Station_ID", "Observation_Type", "Observation_Subtype",
            "Latitude", "Longitude", "Pressure", "Time",
            "Prep_QC_Mark", "Prep_Use_Flag", "Analysis_Use_Flag",
        ]

        with Dataset(path) as nc:
            data = {col: self.read_nc_var(nc, col) for col in base_cols if col in nc.variables}
            data["Datetime"] = getattr(nc, "date_time")

            if variable in ("u", "v"):
                obs_name = f"{variable}_Observation"
                omf_name = f"{variable}_Obs_Minus_Forecast_adjusted"
                data["Observation"] = self.read_nc_var(nc, obs_name)
                data["Obs_Minus_Forecast_adjusted"] = self.read_nc_var(nc, omf_name)
            else:
                data["Observation"] = self.read_nc_var(nc, "Observation")
                data["Obs_Minus_Forecast_adjusted"] = self.read_nc_var(
                    nc, "Obs_Minus_Forecast_adjusted"
                )

        df = pd.DataFrame(data)
        df["var_id"] = var_id
        df["Longitude"] = np.where(df["Longitude"] > 180, df["Longitude"] - 360, df["Longitude"])
        return df

    @staticmethod
    def read_nc_var(nc: Dataset, name: str):
        arr = nc.variables[name][:]

        if name in {"Station_ID", "Observation_Class"}:
            return np.array([
                bytes(row).decode("utf-8", "ignore").strip()
                for row in arr
            ])

        return np.asarray(arr)

    def filter_wdqms_type(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.loc[df["Observation_Type"].isin(self.config.obs_types)].copy()

        if self.wdqms_type in {"SYNOP", "MARINE"}:
            df.loc[
                (df["var_id"] == 110)
                & (df["Obs_Minus_Forecast_adjusted"].abs() > 200),
                "Obs_Minus_Forecast_adjusted",
            ] = 9999.9

            df.loc[
                (df["var_id"] != 110)
                & (df["Obs_Minus_Forecast_adjusted"].abs() > 500),
                "Obs_Minus_Forecast_adjusted",
            ] = 9999.9

        return df

    def add_observation_datetime(self, df: pd.DataFrame) -> pd.DataFrame:
        base = pd.to_datetime(df["Datetime"].astype(str), format="%Y%m%d%H")
        obs_dt = base + pd.to_timedelta(df["Time"], unit="h")

        return df.assign(
            obs_datetime=obs_dt,
            yyyymmdd=obs_dt.dt.strftime("%Y%m%d"),
            HHMMSS=obs_dt.dt.strftime("%H%M%S"),
        )

    def filter_to_cycle_window(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        WDQMS files are centred on 00/06/12/18 UTC windows.
        For GSI diag files, Time is hours relative to analysis.
        Keep -3 <= Time < +3.
        """
        return df.loc[(df["Time"] >= -3.0) & (df["Time"] < 3.0)].copy()

    def add_required_missing_fields(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        WDQMS v5/v4 require Wigos_Id and Timeliness.
        If unavailable, use missing values.
        """
        df = df.copy()

        if "Wigos_Id" not in df:
            df["Wigos_Id"] = "99999"

        if "Timeliness" not in df:
            df["Timeliness"] = -9999

        return df

    def add_status_flag(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Map GSI flags to WDQMS top-level status flags.
        This preserves the main current behavior but uses np.select.
        """
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
            3,  # Never Used by DA / flagged non-use
            7,  # Quality Issue
        ]

        return df.assign(
            StatusFlag=np.select(conditions, choices, default=1).astype(int)
        )

    def add_relative_humidity_departures(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Convert q O-B departures to RH O-B departures where matching
        temperature exists at the same station/location/pressure/time.
        """
        q_id = self.config.variable_ids["q"]
        t_id = self.config.variable_ids["t"]

        q = df.loc[df["var_id"] == q_id].copy()
        t = df.loc[df["var_id"] == t_id].copy()

        if q.empty or t.empty:
            return df

        keys = ["Station_ID", "Latitude", "Longitude", "Pressure", "Time", "Datetime"]

        q = q.drop_duplicates(keys)
        t = t.drop_duplicates(keys)

        merged = q.merge(
            t[keys + ["Observation", "Obs_Minus_Forecast_adjusted"]],
            on=keys,
            suffixes=("_q", "_t"),
            how="inner",
        )

        if merged.empty:
            return df

        q_obs = merged["Observation_q"].astype(float)
        t_obs = merged["Observation_t"].astype(float)
        p_hpa = merged["Pressure"].astype(float)

        q_ges = q_obs - merged["Obs_Minus_Forecast_adjusted_q"].astype(float)
        t_ges = t_obs - merged["Obs_Minus_Forecast_adjusted_t"].astype(float)

        rh_obs = q_obs / self.qsat(t_obs, p_hpa)
        rh_ges = q_ges / self.qsat(t_ges, p_hpa)

        merged["Obs_Minus_Forecast_adjusted"] = rh_obs - rh_ges
        merged["Observation"] = rh_obs
        merged["var_id"] = q_id

        replacement = merged[df.columns]
        non_q = df.loc[df["var_id"] != q_id]
        unmatched_q = q.merge(merged[keys], on=keys, how="left", indicator=True)
        unmatched_q = unmatched_q.loc[unmatched_q["_merge"] == "left_only", df.columns]

        return pd.concat([non_q, replacement, unmatched_q], ignore_index=True)

    @staticmethod
    def qsat(t_k: pd.Series, p_hpa: pd.Series) -> pd.Series:
        """
        Saturation specific humidity in same units as q input if q is g/kg.
        Uses Bolton-style saturation vapour pressure over water.
        """
        t_c = t_k - 273.15
        es_hpa = 6.112 * np.exp((17.67 * t_c) / (t_c + 243.5))
        qsat_kgkg = 0.622 * es_hpa / (p_hpa - 0.378 * es_hpa)
        return qsat_kgkg * 1000.0

    def create_surface_output(self, df: pd.DataFrame) -> pd.DataFrame:
        out = (
            df.assign(
                Centre_id=self.centre_id,
                CodeType=df["Observation_Type"].astype(int),
                Bg_dep=df["Obs_Minus_Forecast_adjusted"],
            )
            .rename(columns={
                "Station_ID": "Station_id",
                "Latitude": "latitude",
            })
            .reindex(columns=SURFACE_COLUMNS)
            .sort_values(["Station_id", "yyyymmdd", "HHMMSS", "var_id"])
            .reset_index(drop=True)
        )

        return self.format_output(out, ["latitude", "Longitude", "Bg_dep"])

    def create_temp_output(self, df: pd.DataFrame) -> pd.DataFrame:
        records = []

        group_cols = ["Station_ID", "yyyymmdd", "HHMMSS"]

        for (station, ymd, hms), profile in df.groupby(group_cols, sort=False):
            lat, lon = self.profile_location(profile)
            last_level = profile["Pressure"].min()
            codetype = int(profile["Observation_Type"].iloc[0])
            wigos_id = str(profile["Wigos_Id"].iloc[0])
            timeliness = int(profile["Timeliness"].iloc[0])

            # Surface pressure, if present
            ps_id = self.config.variable_ids["ps"]
            ps = profile.loc[profile["var_id"] == ps_id]
            if not ps.empty:
                row = ps.iloc[0]
                records.append({
                    "Station_id": station,
                    "yyyymmdd": ymd,
                    "HHMMSS": hms,
                    "latitude": lat,
                    "Longitude": lon,
                    "StatusFlag": int(ps["StatusFlag"].min()),
                    "Centre_id": self.centre_id,
                    "var_id": ps_id,
                    "Mean_Bg_dep": row["Obs_Minus_Forecast_adjusted"],
                    "Std_Bg_dep": 0.0,
                    "Levels": "Surf",
                    "LastRepLevel": -999.99,
                    "CodeType": codetype,
                    "Wigos_Id": wigos_id,
                    "Timeliness": timeliness,
                })

            for var_id in sorted(set(profile["var_id"]) - {ps_id}):
                var_profile = profile.loc[profile["var_id"] == var_id]

                # Optional surface row for variable at maximum pressure
                if not ps.empty:
                    surface_pressure = ps["Pressure"].max()
                    surf = var_profile.loc[var_profile["Pressure"] == surface_pressure]
                    if not surf.empty:
                        records.append(self.temp_layer_record(
                            station, ymd, hms, lat, lon, var_id,
                            surf, "Surf", -999.99, codetype, wigos_id, timeliness
                        ))

                trop = var_profile.loc[var_profile["Pressure"] >= 100]
                if not trop.empty:
                    records.append(self.temp_layer_record(
                        station, ymd, hms, lat, lon, var_id,
                        trop, "Trop", last_level, codetype, wigos_id, timeliness
                    ))

                stra = var_profile.loc[var_profile["Pressure"] < 100]
                if not stra.empty:
                    records.append(self.temp_layer_record(
                        station, ymd, hms, lat, lon, var_id,
                        stra, "Stra", last_level, codetype, wigos_id, timeliness
                    ))

        out = pd.DataFrame.from_records(records, columns=TEMP_COLUMNS)
        out = out.sort_values(["Station_id", "yyyymmdd", "HHMMSS", "var_id", "Levels"])
        out = out.reset_index(drop=True)

        return self.format_output(
            out,
            ["latitude", "Longitude", "Mean_Bg_dep", "Std_Bg_dep", "LastRepLevel"],
        )

    @staticmethod
    def profile_location(profile: pd.DataFrame) -> tuple[float, float]:
        """
        Prefer surface pressure location. Otherwise use highest-pressure level.
        """
        ps = profile.loc[profile["var_id"] == 110]
        if not ps.empty:
            row = ps.sort_values("Pressure", ascending=False).iloc[0]
        else:
            row = profile.sort_values("Pressure", ascending=False).iloc[0]

        return float(row["Latitude"]), float(row["Longitude"])

    def temp_layer_record(
        self,
        station: str,
        ymd: str,
        hms: str,
        lat: float,
        lon: float,
        var_id: int,
        layer: pd.DataFrame,
        level_name: str,
        last_rep_level: float,
        codetype: int,
        wigos_id: str,
        timeliness: int,
    ) -> dict:
        dep = layer["Obs_Minus_Forecast_adjusted"].astype(float)

        return {
            "Station_id": station,
            "yyyymmdd": ymd,
            "HHMMSS": hms,
            "latitude": lat,
            "Longitude": lon,
            "StatusFlag": int(layer["StatusFlag"].min()),
            "Centre_id": self.centre_id,
            "var_id": int(var_id),
            "Mean_Bg_dep": dep.mean(),
            "Std_Bg_dep": 0.0 if len(dep) == 1 else dep.std(ddof=1),
            "Levels": level_name,
            "LastRepLevel": last_rep_level,
            "CodeType": codetype,
            "Wigos_Id": wigos_id,
            "Timeliness": timeliness,
        }

    @staticmethod
    def format_output(df: pd.DataFrame, float_cols: list[str]) -> pd.DataFrame:
        df = df.copy()

        for col in float_cols:
            df[col] = df[col].astype(float).map(lambda x: f"{x:.4f}")

        df["StatusFlag"] = df["StatusFlag"].astype(int)
        df["var_id"] = df["var_id"].astype(int)
        df["CodeType"] = df["CodeType"].astype(int)
        df["Timeliness"] = df["Timeliness"].astype(int)

        return df

    def write_wdqms_csv(self, df: pd.DataFrame, date: str, cycle: str) -> Path:
        start, end = CYCLE_WINDOWS[cycle]
        filename = self.outdir / f"{self.centre_id}_{self.wdqms_type}_{date}_{cycle}.csv"

        with filename.open("w", newline="") as f:
            f.write(f"# TYPE={self.wdqms_type}\n")
            f.write(f"#An_date= {date}\n")
            f.write(f"#An_time= {cycle}\n")
            f.write(f"#An_range=] {start} to {end} ]\n")
            f.write(f"{STATUS_TEXT}\n")
            f.write("#" + ",".join(df.columns) + "\n")
            df.to_csv(f, index=False, header=False)

        logging.info("Wrote %s", filename)
        return filename


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input-list", nargs="+", required=True)
    parser.add_argument("-t", "--type", choices=WDQMS_CONFIG.keys(), required=True)
    parser.add_argument("-o", "--outdir", required=True)
    parser.add_argument("--centre-id", default="NCEP")
    parser.add_argument("-d", "--debug", action="store_const",
                        dest="loglevel", const=logging.DEBUG,
                        default=logging.WARNING)
    parser.add_argument("-v", "--verbose", action="store_const",
                        dest="loglevel", const=logging.INFO)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = WDQMS(
        inputfiles=args.input_list,
        wdqms_type=args.type,
        outdir=args.outdir,
        centre_id=args.centre_id,
        loglevel=args.loglevel,
    ).run()

    print(output)


if __name__ == "__main__":
    main()
