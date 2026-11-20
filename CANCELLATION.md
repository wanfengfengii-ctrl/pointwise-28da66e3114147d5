# 压力采集工单撤办

README.md 说明现有站库、录制器和原调用方式。设备原端口为同步 SQLite 调用。两个会话使用各自的空闲连接，可以访问同一对站库和设备库。设备请求号的 `capture/` 前缀仍由站库的采集工单专用。

## 撤办和续清

- `capture.cancel.cancel(station, order_id, recorder=None)` 按原工单号办理，返回与 `shift.lookup.lookup` 同形的 `LookupResult`。未知工单返回 `unknown`。撤办胜出返回 `cancelled`，`signed` 为空。签发先提交时返回 `signed` 和原成果及首执。可在设备离线时提交撤办。传入设备连接时允许顺带续清，返回值仍表示站库裁决。同号重试沿用已提交的裁决。
- `capture.cancel.cleanup_cancelled(station, recorder, order_id)` 用在线设备连接重试原单保管清理，返回字符串 `pending` 或 `cleared`。未知号抛出 `KeyError`，未撤办的工单抛出 `ValueError`，均无清理动作。失答后可重试原单。只有能够确认该原请求已无活跃保管、且原请求迟到也不会再建立保管时，才可返回 `cleared`。原请求已存在、尚未执行和仅暂时查不到的情况都可能出现在续办链路中。
- 撤办与成功签发以站库中实际先提交的一方为准。写入中断并回滚的签发没有胜出。撤办胜出后，同绑定重登的 `Registration` 和续办的 `LookupResult` 均返回 `cancelled`。同号改绑仍按旧规则拒绝。独立实收保持 `Publication` 返回形状，撤办后即使收到完整真实证据，`signed` 也为空，站库没有该单的成功成果或首执。
- 停源时，原按号查询与原班次清单列出 `cancelled`。该班次项无成功首执或峰值。其他工单已有的独立保管仍可读，按原规则继续录制和签发。清理只针对撤单绑定的原请求。

## 一次录制，两张工单

默认恒等规程录制 P，首段为 7，尾段为 11，出处分别为 `P/segment-1`、`P/segment-2`。A、B 对 P 分别登记，B 已取得独立保管。A 已实收首段。设备可能已经为 A 建立保管但回包丢失，也可能有另一会话停在 A 的设备动作前。另一会话可先取出真尾段，再延迟直接投递。

A 先撤办时，重投真实尾段后 A 仍为 `cancelled`，无成果或首执。恢复设备并续清，返回 `cleared` 后，A 的原请求在迟到调用结束及重开后均无活跃保管。B 仍签出 `[7, 11]` kPa、峰值 11、两段原出处和 `receipt/B`。若 A 的成果和首执先提交，撤办应复领同一签件。若签发在两次写入之间中断并回滚，随后撤办可胜出，重试尾段仍无 A 的成功件。按实际提交顺序核验这两种终局。

`python3 -B -m cancellation_fixture` 在两份临时库上观察当前版本的保管、独立实收及失答边界。夹具的 `Pause` 可暂停一次原端口调用，`lose_reply_after` 在实际调用完成后丢弃回复。结合登记已有的 `lose_reply=True` 和归档已有的 `interrupt_publication=True` 可以构造上述交错。夹具展示的是原业务；新增交割结果由交付后的核验入口检查。

## 运行交付

保留 `python3 -B check_project.py` 和 `python3 -B -m pulse_demo` 可运行。功能交付时沿用 `.pointwise-runtime.json`。清单的 `version` 为 1，`kind` 为 `cli`，`setup` 收纳 argv 数组列表，`verify` 和 `demo` 各为一个 argv 数组。核验入口运行原回归并检查上述撤办先后、迟发和失答续清，失败时非零退出。演示入口实际执行撤 A、续清、B 签发以及停源后按号和班次查询。
