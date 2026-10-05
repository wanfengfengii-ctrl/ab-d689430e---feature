# Toolpath Audit Service

精密探针程序下发运动控制器前的离线审计：校验 G 代码、把轨迹统一换算为毫米
线段、确认整条轨迹（不只是终点）留在闭合工作空间内、且不接触任何夹具禁入
长方体的内部或边界。

## 接口

### `GET /health`

健康检查，返回 `{"status": "ok"}`，供容器 HEALTHCHECK / Compose
`condition: service_healthy` 使用。

### `POST /api/toolpaths/audit`

请求体（JSON）：

| 字段 | 说明 |
| --- | --- |
| `initial_position_mm` | `{x,y,z}`，初始毫米坐标，必须位于工作空间闭区间内 |
| `workspace` | `{min:{x,y,z}, max:{x,y,z}}`，闭合工作空间 |
| `forbidden_regions` | 至多 20 个 `{bounds:{min,max}}` 闭合长方体（可为空数组） |
| `program` | G 代码字符串，至多 5000 行 |

成功 `200`：

```json
{
  "status": "accepted",
  "segments": [
    {"start": {"x": "0", "y": "0", "z": "0"},
     "end":   {"x": "25.4", "y": "0", "z": "0"},
     "motion": "G0", "line": 1}
  ],
  "final_position_mm": {"x": "25.4", "y": "0", "z": "0"}
}
```

失败：

* `400 invalid_request` — 请求结构/数值问题；
* `422 program_error` — 程序词法/语义错误，带首个违规 `line` 与 `reason`；
* `422 outside_workspace` — 线段端点越出闭合工作空间，带 `line`；
* `422 forbidden_contact` — 线段接触禁入区内部或边界，带 `line` 与
  `forbidden_region`（1 基编号）。

任何失败都不返回 `segments` / `final_position_mm`，即不存在可下发的部分轨迹。

## G 代码规则

* 仅接受：`G20`（英寸）/`G21`（毫米）、`G90`（绝对）/`G91`（相对）、
  `G0`/`G1` 直线运动、`X/Y/Z` 规范十进制参数；
* 参数须为规范有限十进制（如 `1`、`-2.5`、`1.0e2`），拒绝 `NaN`、
  `Infinity`、下划线、十六进制；
* 分号 `;` 起到行尾为注释；空行与纯注释行不产生运动；
* 模态设置跨行持续生效；同一行的新设置先作用于该行坐标；
* 同一行同类模态冲突（G20/G21、G90/G91、G0/G1）、轴重复、非法词均报错；
* 未建立运动/单位/坐标模式就给出坐标属于错误；
* 所有错误定位到**原始行号**（从 1 开始）。

## 精确性

全部几何运算使用 `fractions.Fraction`：25.4 = 127/5，英寸换算与相对移动
累加均为精确有理数；线段-长方体相交采用有理数 slab 裁剪（Liang-Yu），
斜穿、贴面、穿过棱/角都稳定裁决。输出经十进制精确还原为规范字符串（分母只
含因子 2、5，必然有限）。

## 运行

```bash
# 宿主机端口可配置（默认 8080）
HOST_PORT=9090 docker compose up --build api
curl -s localhost:9090/health
```

## 一次性 verify 服务

`verify` 服务等待 `api` 健康后，在容器内依次完成：字节码构建检查、
`pytest` 代码测试（含全部边界/碰撞用例）、含**英寸相对移动**与斜向碰撞
的 API 冒烟，并以退出码报告结果：

```bash
docker compose build
docker compose up --build verify      # 退出码 0 表示全部通过
docker compose rm -f verify           # 清理一次性容器
```
