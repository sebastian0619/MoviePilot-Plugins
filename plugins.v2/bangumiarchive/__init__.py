"""
BangumiArchive插件
用于自动归档完结/连载番剧
"""
from typing import Any, Dict, List, Tuple
from app.core.config import settings
from app.core.event import eventmanager, Event, EventType
from app.plugins import _PluginBase
from app.schemas.types import MediaType
from app.log import logger
from app.helper.module import ModuleHelper
from datetime import datetime
import os
import shutil
from pathlib import Path
from app.helper.notification import NotificationHelper

class BangumiArchive(_PluginBase):
    # 插件基础信息
    plugin_name = "番剧归档"
    plugin_desc = "自动检测并归档完结/连载的番剧"
    plugin_version = "1.0"
    plugin_author = "Sebas0619"
    plugin_config_prefix = "bangumiarchive_"
    plugin_order = 21
    auth_level = 1

    # 配置信息
    _enabled = False
    _cron = None
    _paths = None
    _test_mode = False
    _notify = False
    _bidirectional = False

    # 完结状态列表
    END_STATUS = [
        'ended',           # 正常完结
        'canceled',        # 被取消
        'completed',       # 完成
        'released',        # 已发布（适用于剧场版）
        'discontinued'     # 停播
    ]
    
    # 在类中初始化
    meta_helper = None
    
    def init_plugin(self, config: dict = None):
        self.meta_helper = ModuleHelper().get_meta_helper()
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._paths = config.get("paths")
            self._test_mode = config.get("test_mode")
            self._notify = config.get("notify")
            self._bidirectional = config.get("bidirectional")

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """配置表单"""
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 3,
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 3,
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'test_mode',
                                            'label': '测试模式'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 3,
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify',
                                            'label': '发送通知'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 3,
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'bidirectional',
                                            'label': '双向监控'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VTextField',
                        'props': {
                            'model': 'cron',
                            'label': '定时执行',
                            'placeholder': '0 0 * * *'
                        }
                    },
                    {
                        'component': 'VTextarea',
                        'props': {
                            'model': 'paths',
                            'label': '目录配置',
                            'placeholder': '连载目录:完结目录\n例如：/media/anime/airing:/media/anime/ended'
                        }
                    }
                ]
            }
        ], {
            'enabled': False,
            'test_mode': False,
            'notify': False,
            'bidirectional': False,
            'cron': '0 0 * * *',
            'paths': ''
        }

    def __send_notification(self, title: str, text: str):
        """
        发送通知
        """
        if self._notify:
            NotificationHelper().send_message(
                title=title,
                text=text
            )

    def __is_series_ended(self, tmdb_id: int) -> tuple:
        """
        检查剧集是否完结
        返回 (是否完结, 状态描述)
        """
        if not tmdb_id:
            return False, "无TMDB ID"
        
        try:
            # 调用TMDB API
            tmdb_info = self.chain.tmdb.get_tv_detail(tmdb_id)
            if not tmdb_info:
                return False, "无TMDB信息"
            
            status = tmdb_info.get('status', '').lower()
            
            # 检查完结状态
            if status in self.END_STATUS:
                return True, status
            
            # 检查最后播出日期
            last_air_date = tmdb_info.get('last_air_date')
            if last_air_date:
                try:
                    last_date = datetime.strptime(last_air_date, '%Y-%m-%d')
                    # 如果最后播出超过1年，也认为是完结
                    if (datetime.now() - last_date).days > 365:
                        return True, f"最后播出日期 {last_air_date}"
                except:
                    pass
            
            return False, status
            
        except Exception as e:
            logger.error(f"获取TMDB信息出错: {str(e)}")
            return False, f"错误: {str(e)}"

    def __save_history(self, source: str, target: str, title: str, tmdb_id: int, status: str):
        """
        保存移动历史
        """
        try:
            TransferHistory(
                source_path=source,
                target_path=target,
                media_name=title,
                tmdb_id=tmdb_id,
                status=status,
                transfer_type="bangumiarchive",
                create_time=datetime.now()
            ).save()
        except Exception as e:
            logger.error(f"保存历记录失败: {str(e)}")

    def __process_directory(self, source_dir: str, target_dir: str, check_ended: bool = True):
        """
        处理目录
        @param source_dir: 源目录
        @param target_dir: 目标目录
        @param check_ended: True检查完结->移动到完结目录，False检查连载->移动到连载目录
        """
        if not os.path.exists(source_dir):
            logger.error(f"源目录不存在: {source_dir}")
            return
        
        if not self._test_mode and not os.path.exists(target_dir):
            os.makedirs(target_dir)

        moved_items = []  # 记录移动的项目，用于通知

        # 遍历源目录
        for item in os.listdir(source_dir):
            item_path = os.path.join(source_dir, item)
            if not os.path.isdir(item_path):
                continue

            # 获取元数据
            meta_info = self.meta_helper.get_meta_info(item)
            if not meta_info.tmdb_id:
                logger.warning(f"无法解析TMDB ID: {item}")
                continue

            # 检查状态
            is_ended, status = self.__is_series_ended(meta_info.tmdb_id)
            should_move = is_ended if check_ended else not is_ended

            if should_move:
                target_path = os.path.join(target_dir, item)
                move_type = "完结" if check_ended else "连载"
                
                if self._test_mode:
                    logger.info(f"[测试模式] 将移动{move_type}剧集: {item} ({status})")
                    self.__save_history(
                        item_path, 
                        target_path, 
                        item, 
                        meta_info.tmdb_id, 
                        f"测试模式 - {status}"
                    )
                    moved_items.append(f"{item} ({status})")
                else:
                    try:
                        shutil.move(item_path, target_path)
                        logger.info(f"已移动{move_type}剧集: {item} ({status})")
                        self.__save_history(
                            item_path, 
                            target_path, 
                            item, 
                            meta_info.tmdb_id, 
                            status
                        )
                        moved_items.append(f"{item} ({status})")
                    except Exception as e:
                        logger.error(f"移动失败 {item}: {str(e)}")
            else:
                logger.debug(f"保持不变的剧集: {item} ({status})")

        # 发送批量通知
        if moved_items:
            move_type = "完结" if check_ended else "连载"
            self.__send_notification(
                title=f"番剧归档 - {move_type}剧集移动{'(测试)' if self._test_mode else ''}",
                text="\n".join([f"- {item}" for item in moved_items])
            )

    @eventmanager.register(EventType.PluginAction)
    def check_and_move(self, event: Event = None):
        """
        定时任务
        """
        if not self._enabled:
            return
        
        if not self._paths:
            return

        logger.info(f"开始归档任务 [{'测试模式' if self._test_mode else '正常模式'}]")
        
        # 处理每一对目录配置
        for path_pair in self._paths.splitlines():
            if not path_pair.strip():
                continue
            
            try:
                airing_dir, ended_dir = path_pair.split(':')
                airing_dir = airing_dir.strip()
                ended_dir = ended_dir.strip()
                
                # 连载 -> 完结
                logger.info(f"检查连载->完结: {airing_dir} -> {ended_dir}")
                self.__process_directory(airing_dir, ended_dir, check_ended=True)
                
                # 完结 -> 连载（如果启用双向监控）
                if self._bidirectional:
                    logger.info(f"检查完结->连载: {ended_dir} -> {airing_dir}")
                    self.__process_directory(ended_dir, airing_dir, check_ended=False)
                
            except Exception as e:
                logger.error(f"处理目录出错: {str(e)}")
                self.__send_notification(
                    title="番剧归档 - 错误",
                    text=f"处理目录时出错: {str(e)}"
                )

    def get_page(self) -> List[dict]:
        """
        插件页面
        """
        # 获取最近10条历史记录
        histories = TransferHistory.select().where(
            TransferHistory.transfer_type == "bangumiarchive"
        ).order_by(TransferHistory.create_time.desc()).limit(10)

        return [
            {
                'component': 'VCard',
                'content': [
                    {
                        'component': 'VCardTitle',
                        'props': {
                            'class': 'text-h6',
                        },
                        'text': '最近归档记录'
                    },
                    {
                        'component': 'VCardText',
                        'props': {
                            'class': 'pa-0',
                        },
                        'content': [
                            {
                                'component': 'VTable',
                                'props': {
                                    'hover': True,
                                },
                                'content': [
                                    {
                                        'component': 'thead',
                                        'content': [
                                            {
                                                'component': 'tr',
                                                'content': [
                                                    {
                                                        'component': 'th',
                                                        'text': '时间'
                                                    },
                                                    {
                                                        'component': 'th',
                                                        'text': '剧集'
                                                    },
                                                    {
                                                        'component': 'th',
                                                        'text': '状态'
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'tbody',
                                        'content': [
                                            {
                                                'component': 'tr',
                                                'content': [
                                                    {
                                                        'component': 'td',
                                                        'text': history.create_time.strftime('%Y-%m-%d %H:%M:%S')
                                                    },
                                                    {
                                                        'component': 'td',
                                                        'text': history.media_name
                                                    },
                                                    {
                                                        'component': 'td',
                                                        'text': history.status
                                                    }
                                                ]
                                            } for history in histories
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ] 

    def get_state(self) -> bool:
        return self._enabled
    
    def get_command(self) -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def stop_service(self):
        pass