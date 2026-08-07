# Atlas Knowledge 平台指南

平台支持 PDF、DOCX、XLSX、Markdown、TXT、CSV 和 HTML 文件，单个上传文件上限为 50 MB。

检索链路同时使用向量召回与 BM25 关键词召回，通过 RRF 融合，并可使用 lexical reranker 或交叉编码器重排。

系统每天在 UTC 02:30 执行备份，备份保留 30 天。恢复时间目标 RTO 为 4 小时，恢复点目标 RPO 为 24 小时。
