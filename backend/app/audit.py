"""审计日志写入工具。

审计日志必须尽量可靠，但不能因为记录失败反向破坏主业务流程。因此这里使用
独立数据库会话写入，并吞掉审计写入自身的异常。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlmodel import Session

from .database import engine
from .models import AuditLog


def record_audit(
    action: str,
    outcome: str = "success",
    *,
    request: Request | None = None,
    principal: Any | None = None,
    tenant_id: str = "",
    user_id: str = "",
    resource_type: str = "",
    resource_id: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    """写入一条审计事件。

    ``detail`` 只允许放非敏感上下文，例如邮箱、对象 ID、错误类型；不要放密码、
    Token、API Key 或文档正文。
    """

    try:
        if principal is not None:
            tenant_id = tenant_id or principal.tenant_id
            user_id = user_id or principal.user_id

        ip_address = ""
        user_agent = ""
        if request is not None:
            if request.client:
                ip_address = request.client.host
            user_agent = request.headers.get("user-agent", "")[:512]

        log = AuditLog(
            tenant_id=tenant_id or "",
            user_id=user_id or "",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            ip_address=ip_address,
            user_agent=user_agent,
            detail=detail or {},
        )
        with Session(engine) as session:
            session.add(log)
            session.commit()
    except Exception:
        # 审计系统不能拖垮主请求；失败由运行日志/监控告警兜底。
        return
