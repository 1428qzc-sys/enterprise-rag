# 备份恢复演练记录（模板）

| 项 | 值 |
| --- | --- |
| 演练日期 | YYYY-MM-DD |
| 环境 | staging / production |
| 执行人 | |
| RTO 目标 | ≤ ___ 分钟 |
| RPO 目标 | ≤ ___ 小时 |

## Checklist

- [ ] 发布前 `pg_dump` / Qdrant snapshot 已完成并记录文件名
- [ ] 在隔离环境恢复 DB 备份
- [ ] 恢复后 `/api/health` 返回 ok
- [ ] 抽样 KB 文档数量与恢复前一致
- [ ] 抽样检索 + 问答 smoke 通过
- [ ] 记录实际 RTO：___ 分钟

## 附件

- 备份文件：`pg_dump_YYYYMMDD.sql` / `qdrant_snapshot_YYYYMMDD.tar`
- Health JSON：`health-<env>-YYYYMMDD.json`

## 签收

Platform ___ · App Owner ___ · 日期 ___
