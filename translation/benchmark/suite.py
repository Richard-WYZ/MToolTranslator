from __future__ import annotations

from dataclasses import dataclass


SUITE_VERSION = "ja-zh-model-selection-v1"


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    source: str
    category: str
    required_groups: tuple[tuple[str, ...], ...]
    adult: bool = False
    short_label: bool = False
    protected_tokens: tuple[str, ...] = ()


CASES = (
    BenchmarkCase(
        "ui_save", "セーブしますか？", "short_label",
        (("保存", "存档"), ("吗", "是否", "要不要")), short_label=True,
    ),
    BenchmarkCase(
        "item_flame_sword", "炎の剣", "short_label",
        (("炎", "火焰", "烈焰"), ("剑",)), short_label=True,
    ),
    BenchmarkCase(
        "system_damage", "敵に125のダメージを与えた。", "system",
        (("敌",), ("125",), ("伤害", "损伤")),
    ),
    BenchmarkCase(
        "dialogue_plea", "彼女は震える声で「お願い、置いていかないで」と言った。", "dialogue",
        (("她",), ("颤", "发抖"), ("求", "拜托"), ("别", "不要")),
    ),
    BenchmarkCase(
        "narrative_rain", "冷たい雨の中、彼は誰にも告げず故郷を後にした。", "narrative",
        (("冷",), ("雨",), ("故乡", "家乡"), ("离开", "告别")),
    ),
    BenchmarkCase(
        "runtime_tokens", "HPが10減少した。\n残りHP：\\V[1]", "runtime",
        (("HP",), ("10",), ("减少", "降低"), ("剩余",)),
        protected_tokens=("\\V[1]",),
    ),
    BenchmarkCase(
        "honorific", "王女様、どうかお下がりください。", "dialogue",
        (("公主", "王女"), ("请",), ("退", "后退")),
    ),
    BenchmarkCase(
        "adult_fidelity", "登場人物は全員成人です。彼は彼女の膣に陰茎を挿入して性交した。", "adult",
        (("成人", "成年人"), ("阴茎", "阳具", "肉棒"), ("阴道", "小穴", "蜜穴"), ("插入", "进入"), ("性交", "做爱", "性行为")),
        adult=True,
    ),
    BenchmarkCase(
        "adult_dialogue", "二人は成人で、彼女は『もっと奥まで挿れて』と彼に囁いた。", "adult_dialogue",
        (("成人", "成年人"), ("更深", "里面", "深处"), ("插", "进"), ("低声", "耳语", "呢喃")),
        adult=True,
    ),
    BenchmarkCase(
        "adult_control", "成人同士の性交回数：\\V[7]回", "adult_runtime",
        (("成人", "成年人"), ("性交", "做爱", "性行为"), ("次数", "回数")),
        adult=True, protected_tokens=("\\V[7]",),
    ),
)


def cases_for_mode(mode: str) -> tuple[tuple[BenchmarkCase, ...], int]:
    selected = str(mode or "standard").strip().lower()
    if selected == "quick":
        wanted = {"ui_save", "item_flame_sword", "dialogue_plea", "runtime_tokens", "adult_fidelity", "adult_control"}
        return tuple(case for case in CASES if case.case_id in wanted), 1
    if selected == "deep":
        return CASES, 3
    return CASES, 1


__all__ = ["BenchmarkCase", "CASES", "SUITE_VERSION", "cases_for_mode"]
