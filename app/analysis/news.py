"""Analisi delle notizie.

Sentiment dei titoli: lessico finanziario esplicito (parole positive/negative ispirate al
dizionario Loughran-McDonald, con negazioni). È un metodo semplice e trasparente, non un
modello linguistico: va letto come indicazione grezza. Il tono medio per tema arriva da
GDELT (calcolato da GDELT sul testo completo degli articoli).

Intensità: volume di copertura del tema oggi rispetto agli ultimi 90 giorni (z-score).
Novità: quota di titoli non simili a quelli delle 48 ore precedenti (Jaccard sulle parole).
Attendibilità: classificazione delle testate per livello (agenzie e quotidiani finanziari
di riferimento = alta)."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

POS = set("""gain gains rise rises rising rose surge surges surged rally rallies rallied jump jumps jumped soar soars soared
record beat beats beating strong stronger strength growth grow grows grew boost boosts boosted optimism optimistic upbeat
recover recovery rebound rebounds rebounded upgrade upgraded upgrades positive profit profits profitable expand expansion
ease eases easing cut-rates deal agreement ceasefire truce approve approved approval exceed exceeds exceeded outperform""".split())
NEG = set("""fall falls fell falling drop drops dropped decline declines declined slump slumps slumped plunge plunges plunged
tumble tumbles tumbled crash crashes crashed sink sinks sank loss losses lose losing weak weaker weakness fear fears
worry worries worried concern concerns risk risks recession inflationary downgrade downgraded downgrades negative
default defaults bankruptcy crisis war attack attacks strike strikes sanction sanctions tariff tariffs ban bans probe
lawsuit fraud miss misses missed warning warns warned cut-jobs layoffs layoff selloff sell-off turmoil volatile shock""".split())
NEGATIONS = {"no", "not", "never", "without", "despite", "fails", "failed"}

TIER_HIGH = ("reuters.com", "bloomberg.com", "ft.com", "wsj.com", "apnews.com", "cnbc.com", "economist.com",
             "marketwatch.com", "barrons.com", "ilsole24ore.com", "ansa.it", "nikkei.com", "afp.com", "bbc.co.uk",
             "bbc.com", "nytimes.com", "federalreserve.gov", "ecb.europa.eu", "theguardian.com", "handelsblatt.com")
TIER_MED = ("yahoo.com", "investing.com", "fxstreet.com", "forbes.com", "businessinsider.com", "fortune.com",
            "seekingalpha.com", "coindesk.com", "theblock.co", "kitco.com", "oilprice.com", "scmp.com", "aljazeera.com",
            "cointelegraph.com", "zerohedge.com")

_tok = re.compile(r"[a-z][a-z\-']+")


def headline_sentiment(title: str) -> float:
    words = _tok.findall((title or "").lower())
    score, n = 0, 0
    for i, w in enumerate(words):
        s = 1 if w in POS else -1 if w in NEG else 0
        if s:
            if any(x in NEGATIONS for x in words[max(0, i - 3):i]):
                s = -s
            score += s; n += 1
    return 0.0 if n == 0 else float(np.clip(score / max(n, 1), -1, 1))


def source_tier(domain: str) -> str:
    d = (domain or "").lower()
    if any(d.endswith(x) for x in TIER_HIGH):
        return "alta"
    if any(d.endswith(x) for x in TIER_MED):
        return "media"
    return "non classificata"


def _words(t): return set(_tok.findall((t or "").lower())) - {"the", "and", "for", "with", "from", "that", "this", "into"}


def novelty_flags(df: pd.DataFrame, hours=48, thr=0.5) -> list[bool]:
    """True se il titolo non somiglia (Jaccard < thr) a nessun titolo precedente nelle `hours` ore."""
    out = []
    rows = df.sort_values("seendate").to_dict("records")
    seen = []
    for r in rows:
        w = _words(r.get("title"))
        t = r.get("seendate")
        similar = any(len(w & w2) / max(1, len(w | w2)) >= thr for t2, w2 in seen
                      if t is not None and t2 is not None and (t - t2).total_seconds() < hours * 3600)
        out.append(not similar)
        seen.append((t, w))
    return out


def enrich_articles(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.sort_values("seendate").copy()
    df["sentiment"] = df["title"].map(headline_sentiment)
    df["reliability"] = df["domain"].map(source_tier)
    df["novel"] = novelty_flags(df)
    return df.sort_values("seendate", ascending=False)


def topic_summary(tone_vol: pd.DataFrame, arts: pd.DataFrame) -> dict:
    out = {"n_articles": int(len(arts))}
    if tone_vol is not None and not tone_vol.empty:
        tv = tone_vol.dropna()
        daily = tv.resample("1D").mean()
        if len(daily) > 10:
            v = daily["volume"]
            out["coverage_z"] = float((v.iloc[-1] - v.iloc[:-1].mean()) / (v.iloc[:-1].std() or 1))
            out["tone_now"] = float(daily["tone"].iloc[-3:].mean())
            out["tone_90d_mean"] = float(daily["tone"].mean())
            out["tone_z"] = float((out["tone_now"] - daily["tone"].mean()) / (daily["tone"].std() or 1))
            out["tone_series"] = {str(k)[:10]: round(float(x), 3) for k, x in daily["tone"].items() if np.isfinite(x)}
    if not arts.empty:
        out["headline_sentiment_mean"] = float(arts["sentiment"].mean())
        out["share_novel"] = float(arts["novel"].mean())
        out["share_high_reliability"] = float((arts["reliability"] == "alta").mean())
    # rilevanza: copertura insolita + tono insolito + quota di notizie nuove da fonti affidabili
    cz, tz = out.get("coverage_z", 0), abs(out.get("tone_z", 0))
    out["market_relevance"] = float(np.clip(20 * max(cz, 0) + 15 * tz + 30 * out.get("share_novel", 0) * out.get("share_high_reliability", 0), 0, 100))
    out["is_noise"] = bool(cz < 1 and tz < 1)
    return out
