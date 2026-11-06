"""LLM が書く「観察」と、Python が作る成果物のスキーマ。

LLM には原文の引用つきの観察だけを書かせる。点数・照合・判定は Python が行う。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Kind = Literal["確認", "相談", "承認", "判断", "待ち", "差し戻し", "連絡", "その他"]
DEPENDENCY_KINDS = {"確認", "相談", "承認", "判断", "差し戻し"}


# ---------- 抽出（語り1人分） ----------
class Observation(BaseModel):
    line: int = Field(description="引用した行の行番号")
    quote: str = Field(description="原文そのままの引用（1行の中の連続した部分。言い換え禁止）")
    target: str = Field(description="言及された相手の呼び方を原文どおりに（例：課長、佐藤さん、店長）")
    target_hint: str = Field(default="", description="相手が誰かの手がかり（所属・役割・前後の文脈）。分からなければ空")
    kind: Kind
    topic: str = Field(description="何について（例：値引きの可否、職人の割当）")
    frequency: Optional[str] = Field(default=None, description="頻度の表現を原文から。無ければ null")
    wait: Optional[str] = Field(default=None, description="待ちの表現を原文から。無ければ null")


class TermObservation(BaseModel):
    label: str = Field(description="用語・判断基準・指標の呼び方（例：難しい物件、なんとなくの目安）")
    kind: Literal["term", "rule", "metric"]
    line: int
    quote: str = Field(description="原文そのままの引用")
    holder: Optional[str] = Field(default=None, description="その基準を知っている・決めている相手の呼び方。無ければ null")
    defined: bool = Field(description="語りの中で基準・中身が具体的に言葉になっていれば true")


class Extraction(BaseModel):
    observations: list[Observation]
    terms: list[TermObservation]


# ---------- 別名の統合（LLM の提案） ----------
class EntityProposal(BaseModel):
    entity_id: str = Field(description="英小文字とハイフンの短いID（例：koji-tokatsu）")
    display_name: str = Field(description="表示名（例：工事統括の課長（佐藤さん））")
    category: Literal["社内の人・役割", "社外", "物・仕組み"]
    observation_ids: list[str] = Field(description="この相手を指す観察のID")
    reason: str = Field(description="同一と判断した根拠（どの観察のどの文脈か）")
    flow_lane: Optional[str] = Field(default=None, description="公式フローで当たるレーン名（一覧から完全一致で）。無ければ null")
    flow_task: Optional[str] = Field(default=None, description="レーンは無いがタスク名に出てくる場合のタスク名。無ければ null")


class Resolution(BaseModel):
    entities: list[EntityProposal]


# ---------- 反証役 ----------
class Critique(BaseModel):
    entity_id: str
    counter_reasons: list[str] = Field(description="この相手が『定義されていない役割』ではない理由。原文やフローの根拠つき")
    strength: Literal["強い", "弱い", "なし"] = Field(description="反証の強さ")
    still_hub: bool


# ---------- 定義台帳 ----------
class LedgerProposal(BaseModel):
    id: str = Field(description="英小文字とハイフンのID")
    label: str
    kind: Literal["term", "rule", "metric"]
    definition: Optional[str] = Field(default=None, description="語りから言える定義。言葉になっていなければ null")
    status: Literal["draft", "needs_answer"]
    owner_hint: Optional[str] = Field(default=None, description="答えを持っていそうな相手（表示名）")
    evidence_refs: list[str] = Field(description="根拠にした用語観察のID")
    questions: list[str] = Field(description="答えを持つ人への確認の質問（1〜3個）")


class LedgerOut(BaseModel):
    items: list[LedgerProposal]
    implicit_rules: list[LedgerProposal] = Field(
        default_factory=list, description="フローに無い決まりごと（例：〜を超えたら一声かける）"
    )


def json_schema(model: type[BaseModel]) -> dict:
    return model.model_json_schema()
