# 業務フロー 改善前と改善後（仮）

黄色の点線＝「仮」で追加した行（次のヒアリングで確かめる）。

## 改善前（公式）

```mermaid
flowchart LR
  subgraph L0["営業"]
    direction TB
    T0["問い合わせ受付"]
    T1["現地調査"]
    T2["見積作成"]
    T3["受注・契約"]
  end
  subgraph L1["店長"]
    direction TB
    T4["見積の最終承認"]
    T5["値引きの承認"]
  end
  subgraph L2["工務"]
    direction TB
    T6["職人の手配"]
    T7["工程表の作成"]
  end
  subgraph L3["現場"]
    direction TB
    T8["施工管理"]
    T9["追加工事の見積"]
    T10["完了報告"]
  end
  subgraph L4["経理"]
    direction TB
    T11["請求"]
    T12["入金確認"]
  end
  T0 --> T1
  T1 --> T2
  T2 --> T3
  T3 --> T4
  T4 --> T5
  T5 --> T6
  T6 --> T7
  T7 --> T8
  T8 --> T9
  T9 --> T10
  T10 --> T11
  T11 --> T12
  classDef prov fill:#fff3c4,stroke:#c98a00,stroke-dasharray: 5 3,color:#5a3d00
  classDef added fill:#ffe0d6,stroke:#c0392b,color:#5a1a10
```

## 改善後（仮・人の承認待ち）

```mermaid
flowchart LR
  subgraph L0["営業"]
    direction TB
    T0["問い合わせ受付"]
    T1["現地調査"]
    T3["見積作成"]
    T4["受注・契約"]
  end
  subgraph L1["統括（仮）"]
    direction TB
    T2["難あり物件（築年数が古い等）は見積前に予備費の額を営業が統括へ相談（仮）"]
  end
  subgraph L2["課長（仮）"]
    direction TB
    T5["値引きがある場合、店長に上げる前の見積の事前相談（仮）"]
    T7["値引きは先に課長に聞く（仮）"]
    T9["職人の割り振り判断（工程表の前提）（仮）"]
    T15["追加工事を進めてよいかの確認（仮）"]
  end
  subgraph L3["店長"]
    direction TB
    T6["見積の最終承認"]
    T8["値引きの承認"]
  end
  subgraph L4["工務"]
    direction TB
    T10["職人の手配"]
    T11["工程表の作成"]
  end
  subgraph L5["現場（仮）"]
    direction TB
    T12["小さい追加（十万円以下）は現場で決める（仮）"]
    T13["怪しいと思ったら課長に聞く（仮）"]
    T16["十万円超になりそうな追加は施主に言う前に一声かける（仮）"]
  end
  subgraph L6["現場"]
    direction TB
    T14["施工管理"]
    T17["追加工事の見積"]
    T18["完了報告"]
  end
  subgraph L7["経理"]
    direction TB
    T19["請求"]
    T20["入金確認"]
  end
  T0 --> T1
  T1 --> T2
  T2 --> T3
  T3 --> T4
  T4 --> T5
  T5 --> T6
  T6 --> T7
  T7 --> T8
  T8 --> T9
  T9 --> T10
  T10 --> T11
  T11 --> T12
  T12 --> T13
  T13 --> T14
  T14 --> T15
  T15 --> T16
  T16 --> T17
  T17 --> T18
  T18 --> T19
  T19 --> T20
  classDef prov fill:#fff3c4,stroke:#c98a00,stroke-dasharray: 5 3,color:#5a3d00
  classDef added fill:#ffe0d6,stroke:#c0392b,color:#5a1a10
  class T2 prov
  class T5 prov
  class T7 prov
  class T9 prov
  class T12 prov
  class T13 prov
  class T15 prov
  class T16 prov
```
