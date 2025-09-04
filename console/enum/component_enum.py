# -*- coding: utf-8 -*-
from enum import Enum


class ComponentType(Enum):
    stateless_singleton = "stateless_singleton"
    stateless_multiple = "stateless_multiple"
    state_singleton = "state_singleton"
    state_multiple = "state_multiple"
    job = "job"
    cronjob = "cronjob"
    vm = "vm"
    kubeblocks = "kubeblocks_component"

    @staticmethod
    def to_zh(key):
        if key == "stateless_singleton":
            return "无状态单实例"
        if key == "stateless_multiple":
            return "无状态多实例"
        if key == "state_singleton":
            return "有状态单实例"
        if key == "state_multiple":
            return "有状态多实例"
        if key == "kubeblocks_component":
            return "KubeBlocks 组件"


def is_state(component_type):
    if component_type == ComponentType.state_singleton.value or component_type == ComponentType.state_multiple.value:
        return True
    return False


def is_singleton(component_type):
    if component_type == ComponentType.state_singleton.value or component_type == ComponentType.stateless_singleton.value:
        return True
    return False


def is_kubeblocks(component_type):
    """
    判断是否为 KubeBlocks 组件类型
    KubeBlocks 组件是一种特殊的组件类型，其生命周期不由传统的 Rainbond 体系管理，
    而是通过 Block Mechanica 进行管理，用于 KubeBlocks 与 Rainbond 的集成
    
    Args:
        component_type (str): 组件类型字符串
        
    Returns:
        bool: 如果是 KubeBlocks 组件返回 True，否则返回 False
    """
    return component_type == ComponentType.kubeblocks.value


def is_support(component_type):
    if component_type == ComponentType.state_singleton.value \
            or component_type == ComponentType.stateless_singleton.value \
            or component_type == ComponentType.stateless_multiple.value \
            or component_type == ComponentType.state_multiple.value \
            or component_type == ComponentType.job.value \
            or component_type == ComponentType.cronjob.value \
            or component_type == ComponentType.kubeblocks.value:
        return True

    return False


class ComponentSource(Enum):
    THIRD_PARTY = "third_party"
