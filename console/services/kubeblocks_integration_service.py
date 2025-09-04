# -*- coding: utf8 -*-
"""
KubeBlocks 集成服务

提供 KubeBlocks 组件的一站式创建服务，整合了组件创建、集群管理、连接配置等完整流程。
"""

import logging
from django.db import transaction

from console.services.app import app_service
from console.services.app_actions import app_manage_service
from console.services.app_config.env_service import AppEnvVarService
from console.services.app_config.port_service import AppPortService
from console.services.group_service import GroupService
from console.services.kube_blocks_service import kubeblocks_service
from console.exception.main import ServiceHandleException
from www.models.main import TenantServiceInfo

logger = logging.getLogger("kubeblocks_integration")


class KubeBlocksIntegrationService:
    """KubeBlocks 集成服务类"""
    
    def __init__(self):
        self.env_service = AppEnvVarService()
        self.port_service = AppPortService()
        self.group_service = GroupService()
    
    @transaction.atomic
    def create_complete_kubeblocks_component(self, tenant, user, region_name, creation_params):
        """
        一站式创建 KubeBlocks 组件的完整流程
        
        Args:
            tenant: 租户对象
            user: 用户对象  
            region_name: 区域名称
            creation_params: 创建参数，包含组件基础信息和集群配置
            
        Returns:
            tuple: (success, data, error_msg)
                - success: bool 是否成功
                - data: dict 组件信息
                - error_msg: str 错误信息
        """
        new_service = None
        # TODO 或许需要设置 check-uuid,check-event_id, create_status
        try:
            # 第一阶段：创建组件元数据和基础配置
            logger.info(f"开始创建KubeBlocks组件: {creation_params.get('service_cname')}")
            
            new_service = self._create_component_metadata(tenant, user, region_name, creation_params)

            # 第二阶段：创建KubeBlocks集群
            cluster_result = self._create_kubeblocks_cluster(tenant, user, region_name, new_service, creation_params)
            
            # 第二阶段后：更新组件的 k8s_component_name
            self._update_k8s_component_name_from_cluster(new_service, cluster_result)
            
            # 第三阶段：加入应用分组
            self._add_to_application_group(tenant, region_name, creation_params.get('group_id'), new_service.service_id)
            
            # 第四阶段：在Region中创建资源
            self._create_region_service(tenant, new_service, user.nick_name)

            # 第五阶段：配置连接信息（环境变量）
            self._configure_connection_env_vars(tenant, user, region_name, new_service)
            
            # 第六阶段：配置端口信息
            self._configure_service_ports(tenant, user, new_service)
            
            # 第七阶段：构建部署组件
            deploy_result = self._deploy_component(tenant, new_service, user)
            
            # 第八阶段：创建部署关系记录
            from console.repositories.deploy_repo import deploy_repo
            deploy_repo.create_deploy_relation_by_service_id(service_id=new_service.service_id)
            logger.info(f"为组件 {new_service.service_alias} 创建部署关系记录")
            
            logger.info(f"KubeBlocks组件创建成功: {new_service.service_alias}")
            
            # 构建返回数据（与标准组件部署完成后格式一致）
            result_data = self._build_success_response(new_service, deploy_result)
            
            return True, result_data, None
            
        except Exception as e:
            logger.exception(f"创建KubeBlocks组件失败: {str(e)}")
            
            # 清理资源
            if new_service:
                self._cleanup_on_failure(new_service, tenant, region_name)
            
            return False, None, str(e)
    
    def _create_component_metadata(self, tenant, user, region_name, params):
        """创建组件元数据"""
        service_cname = params.get('service_cname', '').strip()
        k8s_component_name = params.get('k8s_component_name', '')
        arch = params.get('arch', 'amd64')
        
        # 调用现有的组件创建方法
        code, msg, new_service = app_service.create_kubeblocks_component(
            region=region_name,
            tenant=tenant,
            user=user,
            service_cname=service_cname,
            k8s_component_name=k8s_component_name,
            arch=arch
        )
        
        if code != 200:
            raise ServiceHandleException(msg=msg, msg_show=msg)

            
        return new_service
    
    def _add_to_application_group(self, tenant, region_name, group_id, service_id):
        """
        将组件加入应用分组
        
        复用现有的应用分组服务，确保与其他组件创建流程保持一致。
        使用推荐的 add_component_to_app 方法而不是已弃用的 add_service_to_group。
        
        Args:
            tenant: 租户对象
            region_name (str): 区域名称
            group_id: 应用分组ID
            service_id (str): 组件服务ID
        
        Raises:
            ErrApplicationNotFound: 当应用分组不存在时抛出异常
        """
        try:
            GroupService.add_component_to_app(
                tenant=tenant,
                region_name=region_name,
                app_id=group_id,
                component_id=service_id
            )
            logger.info(f"成功将组件 {service_id} 加入应用分组 {group_id}")
        except Exception as e:
            logger.error(f"将组件加入应用分组失败: {str(e)}")
            raise ServiceHandleException(
                msg=f"加入应用分组失败: {str(e)}",
                msg_show="加入应用分组失败"
            )
    
    def _create_kubeblocks_cluster(self, tenant, user, region_name, new_service, params):
        """创建KubeBlocks集群"""
        # 构建集群创建参数（与KubeBlocksClustersView.post的参数保持一致）
        cluster_params = {
            "group_id": params.get("group_id"),
            "app_name": params.get("app_name", ""),
            "cluster_name": params.get("cluster_name"),
            "database_type": params.get("database_type"),
            "version": params.get("version"),
            "cpu": params.get("cpu"),
            "memory": params.get("memory"),
            "storage_size": params.get("storage_size"),
            "storage_class": params.get("storage_class", ""),
            "replicas": params.get("replicas", 1),
            "backup_repo": params.get("backup_repo", ""),
            "backup_schedule": params.get("backup_schedule", {}),
            "retention_period": params.get("retention_period", "7d"),
            "termination_policy": params.get("termination_policy", "Delete"),
            "k8s_component_name": new_service.k8s_component_name,
            "arch": params.get("arch", "amd64")
        }
        
        # 创建集群（传递已创建的 kubeblocks 组件对象）
        success, cluster_data = kubeblocks_service.create_cluster(
            tenant, user, region_name, cluster_params, new_service
        )
        
        if not success:
            raise ServiceHandleException(
                msg="KubeBlocks集群创建失败", 
                msg_show="KubeBlocks集群创建失败"
            )
        
        return cluster_data
    
    def _configure_connection_env_vars(self, tenant, user, region_name, new_service):
        """配置数据库连接环境变量"""
        # 配置数据库连接信息，失败时抛出异常
        kubeblocks_service.add_database_env_vars(tenant, new_service, user, region_name)
        logger.info(f"为组件 {new_service.service_alias} 配置连接环境变量成功")
    
    def _configure_service_ports(self, tenant, user, new_service):
        """配置服务端口"""
        # TODO: 
        # 新增一个 region API 从 block mechanica 获取端口信息
        default_port = 3306
        port_alias = "DB"
        
        # 检查端口是否已存在
        existing_ports = self.port_service.get_service_ports(new_service)
        if not existing_ports:
            # 添加默认端口
            code, msg, port_data = self.port_service.add_service_port(
                tenant=tenant,
                service=new_service,
                container_port=default_port,
                protocol="http",
                port_alias=port_alias,
                is_inner_service=True,
                is_outer_service=False,
                k8s_service_name="",
                user_name=user.nick_name
            )
            
            if code != 200:
                logger.error(f"添加默认端口失败: {msg}")
                raise ServiceHandleException(
                    msg=f"添加默认端口失败: {msg}",
                    msg_show="端口配置失败"
                )
            else:
                logger.info(f"为组件 {new_service.service_alias} 添加默认端口 {default_port}")
        else:
            logger.info(f"组件 {new_service.service_alias} 已存在端口配置，跳过端口添加")
    
    def _create_region_service(self, tenant, new_service, user_name):
        """在Region中创建服务资源"""
        try:
            result_service = app_service.create_region_service(
                tenant=tenant,
                service=new_service, 
                user_name=user_name,
                do_deploy=False  # 先不部署，后续单独部署
            )
            logger.info(f"在Region中创建服务资源成功: {new_service.service_alias}")
            return result_service
        except Exception as e:
            logger.error(f"在Region中创建服务资源失败: {str(e)}")
            raise ServiceHandleException(
                msg=f"Region资源创建失败: {str(e)}", 
                msg_show="Region资源创建失败"
            )
    
    def _deploy_component(self, tenant, new_service, user):
        """构建部署组件"""
        try:
            # 设置架构亲和性（在部署前执行）
            from console.services.app_config.arch_service import arch_service
            arch_service.update_affinity_by_arch(
                new_service.arch, tenant, new_service.service_region, new_service
            )
            logger.info(f"为组件 {new_service.service_alias} 设置架构亲和性: {new_service.arch}")
            
            # 调用标准的部署流程
            deploy_result = app_manage_service.deploy(
                tenant=tenant,
                service=new_service,
                user=user,
                oauth_instance=None
            )
            logger.info(f"组件部署成功: {new_service.service_alias}")
            return deploy_result
        except Exception as e:
            logger.error(f"组件部署失败: {str(e)}")
            raise ServiceHandleException(
                msg=f"组件部署失败: {str(e)}", 
                msg_show="组件部署失败"
            )
    
    def _build_success_response(self, new_service, deploy_result):
        """构建成功响应数据（与标准组件部署完成后格式一致）"""
        # 获取最新的服务信息
        updated_service = TenantServiceInfo.objects.get(
            service_id=new_service.service_id,
            tenant_id=new_service.tenant_id
        )
        
        # 构建与app_build.py部署成功后相同格式的响应
        result_data = {
            "service_id": updated_service.service_id,
            "service_cname": updated_service.service_cname,
            "service_alias": updated_service.service_alias,
            "service_key": updated_service.service_key,
            "category": updated_service.category,
            "version": updated_service.version,
            "create_status": updated_service.create_status,
            "deploy_version": updated_service.deploy_version,
            "service_type": updated_service.service_type,
            "extend_method": updated_service.extend_method,
            "min_memory": updated_service.min_memory,
            "min_cpu": updated_service.min_cpu,
        }
        
        # 获取组件所属的应用分组ID（前端跳转必需）
        try:
            group_info = self.group_service.get_service_group_info(updated_service.service_id)
            if group_info:
                result_data["group_id"] = group_info.ID
                logger.info(f"成功获取组件 {updated_service.service_alias} 的分组ID: {group_info.ID}")
            else:
                logger.warning(f"未找到组件 {updated_service.service_id} 的分组关系")
                result_data["group_id"] = None
        except Exception as e:
            logger.error(f"获取组件分组信息失败: {e}")
            result_data["group_id"] = None
        
        # 如果有部署结果，添加部署相关信息
        if deploy_result:
            result_data.update({
                "status": "running",
                "status_cn": "运行中"
            })
        
        return result_data
    
    def _update_k8s_component_name_from_cluster(self, new_service, cluster_result):
        """
        从集群创建结果中更新组件的 k8s_component_name
        
        格式: {cluster_name}-{component_spec_name}
        例如: string-a0e0-mysql
        
        Args:
            new_service: 组件对象
            cluster_result: 集群创建结果数据
        """
        try:
            # 早期返回：检查 cluster_result 有效性
            if not cluster_result or not isinstance(cluster_result, dict):
                logger.warning("集群创建结果无效或为空")
                return
                
            bean = cluster_result.get('bean', {})
            metadata = bean.get('metadata', {})
            spec = bean.get('spec', {})
            
            # 提取并验证必要字段
            cluster_name = metadata.get('name', '').strip()
            component_specs = spec.get('componentSpecs', [])
            
            if not cluster_name or not component_specs:
                return
            
            # 获取并验证组件名称
            component_name = component_specs[0].get('name', '').strip()
            if not component_name:
                logger.warning(f"集群创建结果中未找到有效的组件规格名称: componentSpecs={component_specs}")
                return
            
            # 成功路径：更新组件名称
            new_k8s_component_name = f"{cluster_name}-{component_name}"
            new_service.k8s_component_name = new_k8s_component_name
            new_service.version = component_specs[0].get('serviceVersion', '').strip()
            new_service.create_status = "complete"
            new_service.action = True

            new_service.save()
            logger.info(f"成功更新组件 {new_service.service_alias} 的 k8s_component_name 为: {new_k8s_component_name}")
        except Exception as e:
            raise ServiceHandleException(
                msg=f"更新 k8s_component_name 失败: {str(e)}",
                msg_show="更新 k8s_component_name 失败"
            )
    
    def _cleanup_on_failure(self, new_service, tenant, region_name):
        """失败时清理资源"""
        try:
            if new_service and new_service.service_id:
                # 清理KubeBlocks集群
                kubeblocks_service.delete_kubeblocks_cluster([new_service.service_id], region_name)
                
                # 清理组件（如果已创建）
                if new_service.pk:
                    new_service.delete()
                    
                logger.info(f"清理失败的组件资源: {new_service.service_id}")
        except Exception as e:
            logger.error(f"清理失败资源时出错: {str(e)}")


# 全局实例
kubeblocks_integration_service = KubeBlocksIntegrationService()