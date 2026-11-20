# 风机试验站压力脉冲采集

试验站操作员按测线规程触发压力脉冲。设备按实际段位交付整数原码和设备出处。采集员在录制中登记要留存的物理脉冲，夜班在设备停源后核对原序压力读值、峰值、逐段出处和首执。

## 运行

需要 Python 3.11，使用标准库和 SQLite。在项目根目录运行。

```text
python3 -B check_project.py
python3 -B -m pulse_demo
```

核验入口运行 `tests` 包的完整 `unittest` 套件。演示读取 `example/day.json`，在临时目录建立设备库和站库，随后关闭两库并单独重开站库领取。成功时输出包含输入、手算预期、实测结果及离线班次清单的 `POINTWISE_SCENARIO`；随后在同一批临时库上重放撤办与签发的两种提交顺序，输出 `POINTWISE_CANCELLATION`，覆盖撤 A、续清、B 签发及停源后按号和班次查询。

## 一班试验

1. 开班初始化默认规程。操作员可以配置用户规程及新版本，重复初始化保留已有版本。
2. 设备触发 P，固定物理身份和实际槽位，先录入 P 的段位 1 和原码 7。
3. 采集员在尾段尚未到达时登记 P。工单固定班次、脉冲、段数和规程版本，并请求设备为原工单保留 P。
4. Q 推进同一活动槽并录入 13、29。保管中的 P 还能录入有实际 P 出处的迟到尾段 11。
5. 续办从原保管取回实录。归档验明段位、实际脉冲、出处和原码域。验明的部分实收可跨次提交。
6. 全部段位验明后，归档按段位升序计算 kPa。公式为 `斜率 × 原码 + 截距`，运算及存取保持整数精度。峰值取换算读值的最大值。恒等规程下 P 得到 `[7, 11]` 和峰值 `11`。
7. 站库同次提交结果与首执。采集端据已提交的签件释放设备原保管。若提交中断，先前实收和原保管可供原号续办。
8. 规程修订及设备停源后，夜班按原号领取原成果，按班次核对已签与待收清单。

## Python 接口

用 `storage.open_database(path)` 分别打开独立的站库与模拟设备库。连接由调用者关闭。将空闲连接交给公开操作，操作自行管理事务。一个站库与一个设备库配套使用，设备请求号中的 `capture/` 前缀留给工单。设备停源可以直接关闭设备连接，查询只需站库。

| 接口 | 输入和返回 |
| --- | --- |
| `capture.register.initialize_station(station)` | 安装默认规程及站库表，可重复调用。 |
| `device.triggers.initialize_device(recorder)` | 初始化模拟设备库，可重复调用。 |
| `spec.versions.configure(station, profile)` | `spec.profile.ProfileVersion(line, revision, segment_count, raw_min, raw_max, slope, intercept)` 使用正段数及整数参数。相同版本同参复用，非法值及同版改参抛出 `ValueError`。`get_version(station, (line, revision))` 按固定键读取。 |
| `device.triggers.trigger_pulse(recorder, pulse, slot, profile)` | `device.pulses.PulseIdentity(recorder_name, physical_pulse_name)` 表示一次物理触发，名字必须非空。触发固定槽位和设备录制规程。重用物理身份抛出 `ValueError`。推进活动槽会退役无保管的前一脉冲。 |
| `device.segments.record_segment(recorder, pulse, position, raw_code, origin)` | `SegmentOrigin(pulse, position, slot, source)` 记录实际出处。`None` 表示空段，保持缺位。相同段复投返回原段。越位、越域、错误出处、退役后录制和冲突均抛出 `ValueError`。 |
| `capture.register.register(station, recorder, order_id, pulse, segment_count, profile_key, shift=...)` | 登记针对已触发的物理脉冲。返回 `Registration`，状态为 `pending_confirmation`、`pending`、`cancelled` 或 `signed`。初次登记固定班次，同号同参恢复保管或复领原签件，同号改脉冲、段数或规程版本抛出 `OrderConflict`。已撤办的同号重登只报告 `cancelled`，不再请求设备。确定无法保管的原单仍可在班次清单看见。 |
| `capture.resume.resume(station, recorder, order_id)` | 续领原件并归档，返回与 `lookup` 相同的视图。无证据或缺段时待收。已签单可复领，若设备仍在线可续办尚未完成的释放。`recorder=None` 允许在停源时读取原状态。 |
| `archive.publish.receive_and_publish(station, order_id, segments)` | 独立实收入口。`RecordedSegment` 携带实际脉冲、实际段位、原码和随件设备出处。归档按原工单校验来件，异次、越界及冲突逐段拒绝并报告原因，已验明的部分实收保持。未知工单抛出 `KeyError`。保管已确认且全套实收后可签发。 |
| `shift.lookup.lookup(station, order_id)` | 返回 `unknown`、`pending`、`cancelled` 或 `signed`。签件携带原规程、原码、有序压力、峰值、逐段出处及首执，并核对绑定、段位、出处和峰值。 |
| `shift.manifest.manifest(station, shift)` | 按原班次和工单号字典序列出已签及待收。待收项没有成功首执或峰值。空值及首尾带空白的班次抛出 `ValueError`。 |
| `capture.cancel.cancel(station, order_id, recorder=None)` | 按原工单号撤办，返回与 `lookup` 同形的视图。未知号返回 `unknown`。撤办胜出返回 `cancelled`；签发先提交时返回 `signed` 和原成果及首执。可在设备离线时调用；传入设备连接会顺带清理原保管，但返回值仍是站库裁决。 |
| `capture.cancel.cleanup_cancelled(station, recorder, order_id)` | 用在线设备连接重试原单保管清理，返回 `pending` 或 `cleared`。未知号抛出 `KeyError`，未撤办抛出 `ValueError`。只有原请求已无活跃保管且迟到调用也不会再建立保管时才返回 `cleared`。 |

设备原始保管端口位于 `device.retention` 和 `device.readout`。请求号绑定一个物理脉冲，原号可查询持久终局。读件按原保管返回当前已有原段及缺位。每个保管独立释放，释放后该保管不可重读。最后一份保管释放时设备回收原件，站库保留已签结果。撤办清理会为缺失的原请求留下 `released` 终局，使迟到的同号请也无法再建立保管。

## 确定性验证

样例及测试用事件顺序控制尾段到达。登记的 `lose_reply=True` 在设备提交后丢失响应，`hide_status=True` 让本次查询待查，归档的 `interrupt_publication=True` 在成果与首执写入之间触发 `PublicationInterrupted`。测试在固定阶段关闭及重开真实 SQLite 库。完整回归覆盖重复初始化、原段复投、原码边界、空缺和零、逆序及全负定标、活动槽复用、保管失答、独立异次实收、提交回滚、释放、同号异参和停源班次复核。
