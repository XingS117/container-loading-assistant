# -*- coding: utf-8 -*-
"""测试共享工具。

pack_by_goal：按优化目标打包一次并返回唯一的方案。
改造后每次 API 调用只返回一个方案（优化目标由请求字段决定），
所有目标对比类测试都通过它分别发起计算。
"""
import pytest

from app.models import PackRequest
from app.packing import pack_order


@pytest.fixture
def pack_by_goal():
    def _pack(request: PackRequest, goal: str):
        request.optimization_goal = goal
        response = pack_order(request)
        assert len(response.solutions) == 1, f"{goal} 应只返回一个方案"
        return response.solutions[0]

    return _pack
