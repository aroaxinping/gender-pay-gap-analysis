"""
Cleaning pipeline for the IPUMS-CPS ASEC 2025 extract.

Raw variable coding follows the IPUMS-CPS data dictionary
(https://cps.ipums.org/cps-action/variables/group) — special/missing
codes below are documented there per-variable, not invented here.
"""

import pandas as pd

# IPUMS-CPS missing/NIU (not-in-universe) codes for the variables we use.
INCWAGE_MISSING = {99999999, 99999998}
UHRSWORKT_NIU = {997, 999}  # 997 = hours vary, 999 = NIU
EMPSTAT_EMPLOYED = {10, 12}  # 10 = at work, 12 = has job, not at work last week


def load_raw(path: str) -> pd.DataFrame:
    return pd.read_csv(path)


def clean_pay_gap_data(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    df.columns = df.columns.str.lower()

    # Keep only employed respondents with a usable wage and hours figure.
    df = df[df["empstat"].isin(EMPSTAT_EMPLOYED)]
    df = df[~df["incwage"].isin(INCWAGE_MISSING)]
    df = df[~df["uhrsworkt"].isin(UHRSWORKT_NIU)]
    df = df[df["incwage"] > 0]
    df = df[df["uhrsworkt"] > 0]

    df["sex"] = df["sex"].map({1: "Male", 2: "Female"})

    # Approximate annual hours -> implied hourly wage, for a like-for-like
    # comparison instead of raw annual salary (which conflates pay with
    # full-time/part-time status).
    df["implied_hourly_wage"] = df["incwage"] / (df["uhrsworkt"] * 52)

    df = df.drop_duplicates()
    return df.reset_index(drop=True)


def weighted_median(values: pd.Series, weights: pd.Series) -> float:
    """
    Median that accounts for ASEC sampling weights (asecwt), so the result
    reflects the population the survey samples from, not just the raw rows
    that happened to be in the sample.
    """
    order = values.sort_values().index
    v = values.loc[order].to_numpy()
    w = weights.loc[order].to_numpy()
    cum_w = w.cumsum()
    cutoff = w.sum() / 2
    return v[cum_w >= cutoff][0]


def raw_gap(df: pd.DataFrame, weighted: bool = True) -> float:
    """Unadjusted median wage gap: % less women earn than men, median wage."""
    if weighted:
        medians = df.groupby("sex").apply(
            lambda g: weighted_median(g["incwage"], g["asecwt"]), include_groups=False
        )
    else:
        medians = df.groupby("sex")["incwage"].median()
    return 1 - (medians["Female"] / medians["Male"])


FULL_TIME_HOURS = 35  # standard BLS/Census full-time threshold


def mismatched_comparison_gap(df: pd.DataFrame) -> float:
    """
    The deliberately unfair comparison: part-time women vs. full-time men,
    median annual wage. Included to show exactly how large a number you can
    get from real data by comparing two different things without saying so —
    not because it's a valid measure of the pay gap.
    """
    pt_women = df[(df["sex"] == "Female") & (df["uhrsworkt"] < FULL_TIME_HOURS)]
    ft_men = df[(df["sex"] == "Male") & (df["uhrsworkt"] >= FULL_TIME_HOURS)]
    pt_women_med = weighted_median(pt_women["incwage"], pt_women["asecwt"])
    ft_men_med = weighted_median(ft_men["incwage"], ft_men["asecwt"])
    return 1 - (pt_women_med / ft_men_med)


def full_time_gap(df: pd.DataFrame) -> float:
    """Fair comparison: full-time women vs. full-time men, median annual wage."""
    ft = df[df["uhrsworkt"] >= FULL_TIME_HOURS]
    return raw_gap(ft, weighted=True)


def adjusted_gap(df: pd.DataFrame, control_cols: list[str], weighted: bool = True) -> float:
    """
    Median wage gap after grouping by the given control variables
    (e.g. occupation, hours bucket) and comparing within each group,
    not across the whole population. Weighted by group population size
    (sum of asecwt) rather than raw row counts.
    """
    if weighted:
        med = (
            df.groupby(control_cols + ["sex"])
            .apply(lambda g: weighted_median(g["incwage"], g["asecwt"]), include_groups=False)
        )
        grouped = med.unstack("sex").dropna()
        weights = df.groupby(control_cols)["asecwt"].sum().loc[grouped.index]
    else:
        grouped = df.groupby(control_cols + ["sex"])["incwage"].median().unstack("sex").dropna()
        weights = df.groupby(control_cols).size().loc[grouped.index]
    gap_by_group = 1 - (grouped["Female"] / grouped["Male"])
    return (gap_by_group * weights).sum() / weights.sum()
