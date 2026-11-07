"""画面①の「呼び方をまとめた」：新しく加わった名指しの呼び方だけを出す。"""
from anmoku.render import viewer_data


def cand(name, obs, aliases, score):
    return {"name": name, "category": "社内の人・役割", "observation_ids": obs, "aliases": aliases, "score": score,
            "evidence": [], "absence": 1, "absence_reason": "", "speakers": 1, "deps": 0, "waits": 0, "formula": "",
            "hub_hypothesis": False, "reason": ""}


def stage(k, cands):
    return {"k": k, "added": {"id": f"0{k}", "title": ""}, "candidates": cands}


def detect(stages):
    return {"stages": stages, "flow": {"title": "t", "lanes": []}, "interviews": [], "quotes": {"total": 0, "kept": 0}}


def merged(d):
    return [{c["name"]: c["merged_from"] for c in st["candidates"]} for st in viewer_data(d)["stages"]]


def test_new_named_alias_is_shown_at_the_stage_it_joins():
    d = detect([
        stage(1, [cand("課長", ["01#o1"], {"課長": 1}, 3)]),
        stage(2, [cand("佐藤課長", ["01#o1", "02#o1"], {"課長": 1, "佐藤さん": 2}, 10)]),
        stage(3, [cand("佐藤課長", ["01#o1", "02#o1", "03#o1"], {"課長": 1, "佐藤さん": 2, "統括": 3}, 20)]),
    ])
    m = merged(d)
    assert m[0]["課長"] == [] and m[1]["佐藤課長"] == ["佐藤さん"] and m[2]["佐藤課長"] == ["統括"]


def test_unnamed_observation_moving_in_is_not_a_merge():
    # 営業担当の候補にあった「明示なし」の観察が、次の段階でハブに移っても「まとめた」にしない
    d = detect([
        stage(1, [cand("佐藤課長", ["01#o1"], {"佐藤さん": 1}, 5), cand("営業担当", ["01#o2", "01#o3"], {"営業さん": 1, "明示なし": 1}, 0)]),
        stage(2, [cand("佐藤課長", ["01#o1", "01#o3", "02#o1"], {"佐藤さん": 2, "明示なし": 1}, 9),
                  cand("営業担当", ["01#o2"], {"営業さん": 1}, 0)]),
    ])
    m = merged(d)
    assert m[1]["佐藤課長"] == [] and m[1]["営業担当"] == []


def test_brand_new_candidate_with_two_aliases_is_not_shown_as_merge():
    d = detect([stage(1, [cand("課長", ["01#o1"], {"課長": 1}, 3)]),
                stage(2, [cand("課長", ["01#o1"], {"課長": 1}, 3), cand("佐藤", ["02#o1", "02#o2"], {"佐藤さん": 1, "統括": 1}, 5)])])
    assert merged(d)[1]["佐藤"] == []
