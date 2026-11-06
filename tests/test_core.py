from pathlib import Path

from anmoku.evidence import check_quote, norm
from anmoku.flow import Flow, FlowRow, flow_from_spec, parse_flow, to_markdown, to_mermaid
from anmoku.interviews import Interview
from anmoku.render import link_stages
from anmoku.resolve import apply
from anmoku.schema import EntityProposal, Resolution
from anmoku.score import absence, metrics, rank, score

FLOW = flow_from_spec({
    "title": "テスト",
    "lanes": [
        {"lane": "営業", "owner": "営業担当", "tasks": ["見積作成"]},
        {"lane": "店長", "owner": "店長", "tasks": ["値引きの承認"]},
        {"lane": "経理", "owner": "経理担当（山本課長）", "tasks": ["請求"]},
        {"lane": "工程管理", "owner": "工務課・田中係長", "tasks": ["工程の調整"]},
    ],
})


def iv(lines):
    return Interview(id="01_x", path=Path("01_x.md"), title="t", speaker="小林", lines=lines)


def obs(i, interview, target, kind="確認", wait=None):
    return {"id": f"{interview}#o{i}", "interview": interview, "line": i, "quote": "q", "target": target,
            "kind": kind, "wait": wait, "topic": "t", "speaker": "s"}


# ---------- 引用の実在チェック ----------
def test_quote_exact_and_normalized():
    v = iv(["# 見出し", "小林：店長に上げる前に、いったん課長に聞くんですよ。"])
    assert check_quote(v, "いったん課長に聞くんですよ", 2).ok
    assert check_quote(v, "いったん 課長に聞く", 2).ok  # 空白・句読点の違いは許す


def test_quote_line_fix_and_reject():
    v = iv(["一行目の話です。", "二行目：統括に確認します。"])
    c = check_quote(v, "統括に確認します", 1)
    assert c.ok and c.line == 2
    assert not check_quote(v, "佐藤さんに確認します", 2).ok   # 原文に無い
    assert not check_quote(v, "統括", 2).ok                    # 短すぎる


def test_norm():
    assert norm("「課長」に、聞く。") == "課長に聞く"


# ---------- フロー ----------
def test_flow_roundtrip():
    f = parse_flow(to_markdown(FLOW))
    assert f.lanes == ["営業", "店長", "経理", "工程管理"]
    assert f.rows[2].owner == "経理担当（山本課長）"


def test_mermaid_marks_added_and_provisional():
    v2 = Flow(title="v2", rows=FLOW.rows + [FlowRow("工事統括（仮）", "佐藤課長（仮）", "値引きの事前相談（仮）")])
    m = to_mermaid(v2, base=FLOW)
    assert "class T4 prov" in m and "subgraph" in m


# ---------- フローに無い度合い ----------
def test_absence_lane_and_owner():
    assert absence({"flow_lane": "店長"}, ["店長"], FLOW)[0] == 0.0
    assert absence({}, ["店長"], FLOW)[0] == 0.0                 # レーン名と一致
    assert absence({}, ["田中さん"], FLOW)[0] == 0.0             # 担当欄の名前と一致
    assert absence({}, ["山本課長"], FLOW)[0] == 0.0


def test_absence_generic_title_does_not_match_other_person():
    # 「課長」だけでは、経理の山本課長に当てない
    assert absence({}, ["課長", "統括"], FLOW)[0] == 1.0


def test_absence_task_only():
    assert absence({"flow_task": "工程の調整"}, ["工程の人"], FLOW)[0] == 0.5


# ---------- 点数 ----------
def test_score_formula():
    o = [obs(1, "01", "課長", "相談"), obs(2, "01", "課長", "待ち"), obs(3, "02", "佐藤さん", "判断", wait="夕方まで")]
    m = metrics(o)
    assert (m["speakers"], m["deps"], m["waits"]) == (2, 2, 2)
    assert score(1.0, m) == 2 * 2 + 2 + 2 * 1.5
    assert score(0.0, m) == 0.0


def test_rank_excludes_external_and_unknown():
    o = [obs(1, "01", "課長"), obs(2, "01", "施主さん"), obs(3, "01", "不明")]
    ents = [
        {"id": "hub", "name": "課長", "category": "社内の人・役割", "flow_lane": None, "flow_task": None, "observation_ids": ["01#o1"], "reason": ""},
        {"id": "cust", "name": "施主", "category": "社外", "flow_lane": None, "flow_task": None, "observation_ids": ["01#o2"], "reason": ""},
        {"id": "unk", "name": "不明", "category": "社内の人・役割", "flow_lane": None, "flow_task": None, "observation_ids": ["01#o3"], "reason": ""},
    ]
    r = rank(ents, o, FLOW)
    assert r[0]["id"] == "hub" and r[0]["score"] > 0
    assert {x["id"]: x["score"] for x in r}["cust"] == 0.0
    assert {x["id"]: x["category"] for x in r}["unk"] == "不明"


# ---------- 別名の統合の反映 ----------
def test_apply_validates_ids_and_lanes():
    o = [obs(1, "01", "課長"), obs(2, "02", "佐藤さん"), obs(3, "01", "山本課長")]
    res = Resolution(entities=[
        EntityProposal(entity_id="hub", display_name="工事の課長", category="社内の人・役割",
                       observation_ids=["01#o1", "02#o2", "99#o9"], reason="r", flow_lane="存在しないレーン"),
        EntityProposal(entity_id="hub", display_name="重複", category="社内の人・役割",
                       observation_ids=["01#o1"], reason="r"),  # 割り当て済みのIDは無視
    ])
    ents = apply(res, o, FLOW)
    assert ents[0]["observation_ids"] == ["01#o1", "02#o2"]
    assert ents[0]["flow_lane"] is None
    assert any(e["name"] == "山本課長" for e in ents)  # 割り当て漏れは補う
    assert len(ents) == 2


# ---------- 段階をまたいだ候補のつなぎ ----------
def test_link_stages_tracks_merge():
    def cand(name, ids, s, aliases):
        return {"name": name, "category": "社内の人・役割", "observation_ids": ids, "score": s, "aliases": aliases, "evidence": []}
    stages = [
        {"k": 1, "added": {"id": "01"}, "candidates": [cand("課長", ["a"], 4.5, {"課長": 1})]},
        {"k": 2, "added": {"id": "02"}, "candidates": [cand("課長", ["a"], 4.5, {"課長": 1}), cand("佐藤さん", ["b"], 3, {"佐藤さん": 1})]},
        {"k": 3, "added": {"id": "03"}, "candidates": [cand("工事統括", ["a", "b", "c"], 20, {"課長": 1, "佐藤さん": 1, "統括": 1})]},
    ]
    out = link_stages(stages)
    k1 = out[0]["candidates"][0]["key"]
    last = out[2]["candidates"][0]
    assert last["key"] == k1
    assert last["merged_from"] == ["佐藤さん"]
    assert last["new_aliases"] == ["統括"]


def test_hub_hypothesis_needs_two_speakers():
    o = [obs(1, "01", "課長", "相談"), obs(2, "01", "課長", "待ち"), obs(3, "02", "佐藤さん", "判断"), obs(4, "03", "事務所", "確認")]
    ents = [
        {"id": "hub", "name": "工事の課長", "category": "社内の人・役割", "flow_lane": None, "flow_task": None, "observation_ids": ["01#o1", "01#o2", "02#o3"], "reason": ""},
        {"id": "one", "name": "事務", "category": "社内の人・役割", "flow_lane": None, "flow_task": None, "observation_ids": ["03#o4"], "reason": ""},
    ]
    r = {x["id"]: x for x in rank(ents, o, FLOW)}
    assert r["hub"]["hub_hypothesis"] is True
    assert r["one"]["hub_hypothesis"] is False   # 1人の語りだけ・点数も低い
