"""
MediaArchive插件
用于自动归档媒体文件
"""
from typing import Any, Dict, List, Tuple, NamedTuple, Optional, Set
from datetime import datetime
import time
import os
from pathlib import Path
import shutil
from app.core.config import settings
from app.core.event import eventmanager, Event, EventType
from app.plugins import _PluginBase
from app.log import logger
from app.schemas import NotificationType
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

class MediaThreshold(NamedTuple):
    """媒体归档阈值配置"""
    creation_days: int  # 创建时间阈值（天）
    mtime_days: int     # 修改时间阈值（天）

class MediaArchive(_PluginBase):
    # 插件基础信息
    plugin_name = "媒体文件归档"
    plugin_desc = "自动归档媒体文件到指定目录"
    plugin_version = "1.1"
    plugin_author = "Sebastian0619"
    plugin_icon = "emby.png"
    author_url = "https://github.com/sebastian0619"
    plugin_config_prefix = "mediaarchive_"
    plugin_order = 21
    auth_level = 1

    # 配置信息
    _enabled = False
    _onlyonce = False
    _cron = None
    _source_dir = None
    _target_dir = None
    _test_mode = False
    _notify = False
    
    # 默认阈值配置
    DEFAULT_THRESHOLDS = "电影#20#20\n电影#30#30\n完结动漫#100#45\n完结动漫#120#60\n电视剧#10#90\n综艺#10#10"
    
    # 媒体类型阈值配置 Dict[str, List[MediaThreshold]]
    _thresholds: Dict[str, List[MediaThreshold]] = {}
    
    # 视频文件扩展名
    VIDEO_EXTENSIONS = {
        '.mp4', '.mkv', '.avi', '.ts', '.m2ts',
        '.mov', '.wmv', '.iso', '.m4v', '.mpg',
        '.mpeg', '.rm', '.rmvb'
    }
    
    # 在类中初始化
    _scheduler = None
    _transfer_messages = {
        "success": [],    # 成功记录
        "skipped": [],    # 跳过记录
        "failed": []      # 失败记录
    }

    def init_plugin(self, config: dict = None):
        """插件初始化"""
        try:
            if config:
                self._enabled = config.get("enabled", False)
                self._onlyonce = config.get("onlyonce", False)
                self._cron = config.get("cron", "5 1 * * *")
                self._source_dir = config.get("source_dir", "")
                self._target_dir = config.get("target_dir", "")
                self._test_mode = config.get("test_mode", False)
                self._notify = config.get("notify", False)
                
                # 更新阈值配置
                thresholds_str = config.get("thresholds_str", self.DEFAULT_THRESHOLDS)
                self._thresholds.clear()
                for line in thresholds_str.splitlines():
                    if not line.strip():
                        continue
                    try:
                        media_type, creation_days, mtime_days = line.strip().split("#")
                        if media_type not in self._thresholds:
                            self._thresholds[media_type] = []
                        self._thresholds[media_type].append(MediaThreshold(
                            creation_days=int(creation_days),
                            mtime_days=int(mtime_days)
                        ))
                    except Exception as e:
                        logger.error(f"解析阈值配置失败: {line} - {str(e)}")
                
                # 如果开启立即运行
                if self._enabled and self._onlyonce:
                    logger.info("媒体归档服务启动，立即运行一次...")
                    self.process_all_directories()
                    self._onlyonce = False
                    self.__update_config()
                
                # 周期运行
                if self._enabled and self._cron:
                    try:
                        self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                        self._scheduler.add_job(
                            func=self.process_all_directories,
                            trigger=CronTrigger.from_crontab(self._cron),
                            name="媒体归档"
                        )
                        if self._scheduler.get_jobs():
                            self._scheduler.print_jobs()
                            self._scheduler.start()
                            logger.info(f"周期任务已启动，执行周期：{self._cron}")
                    except Exception as err:
                        logger.error(f"周期任务启动失败：{str(err)}")
                        
        except Exception as e:
            logger.error(f"插件初始化失败: {str(e)}")

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """配置表单"""
        return [
            {
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 3
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
                            'cols': 12,
                            'md': 3
                        },
                        'content': [
                            {
                                'component': 'VSwitch',
                                'props': {
                                    'model': 'onlyonce',
                                    'label': '立即运行一次'
                                }
                            }
                        ]
                    },
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 3
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
                            'cols': 12,
                            'md': 3
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
                    }
                ]
            },
            {
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12
                        },
                        'content': [
                            {
                                'component': 'VTextField',
                                'props': {
                                    'model': 'source_dir',
                                    'label': '源目录',
                                    'placeholder': '输入源目录路径'
                                }
                            }
                        ]
                    }
                ]
            },
            {
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12
                        },
                        'content': [
                            {
                                'component': 'VTextField',
                                'props': {
                                    'model': 'target_dir',
                                    'label': '目标目录',
                                    'placeholder': '输入目标目录路径'
                                }
                            }
                        ]
                    }
                ]
            },
            {
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12
                        },
                        'content': [
                            {
                                'component': 'VTextField',
                                'props': {
                                    'model': 'cron',
                                    'label': '执行周期',
                                    'placeholder': '5位cron表达式，默认：5 1 * * *'
                                }
                            }
                        ]
                    }
                ]
            },
            {
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12
                        },
                        'content': [
                            {
                                'component': 'VTextarea',
                                'props': {
                                    'model': 'thresholds_str',
                                    'label': '阈值配置',
                                    'placeholder': '每行一个配置，格式：类型#创建时间#修改时间\n例如：\n电影#20#20\n完结动漫#100#45\n电视剧#10#90\n综艺#1#1',
                                    'rows': 6,
                                    'persistent-placeholder': True
                                }
                            }
                        ]
                    }
                ]
            }
        ], {
            'enabled': False,
            'onlyonce': False,
            'test_mode': False,
            'notify': False,
            'cron': '5 1 * * *',
            'source_dir': '',
            'target_dir': '',
            'thresholds_str': self.DEFAULT_THRESHOLDS
        }

    def get_state(self) -> bool:
        return self._enabled

    def __get_creation_time(self, path: Path) -> float:
        """获取文件或目录的创建时间"""
        try:
            stat = path.stat()
            return getattr(stat, 'st_birthtime', stat.st_mtime)
        except Exception as e:
            logger.error(f"获取创建时间失败 {path}: {e}")
            return time.time()

    def __get_media_type(self, path: Path) -> str:
        """根据路径判断媒体类型"""
        path_str = str(path)
        if "/电影/" in path_str:
            return "电影"
        elif "/动漫/完结动漫/" in path_str:
            return "完结动漫"
        elif "/电视剧/" in path_str:
            return "电视剧"
        elif "/综艺/" in path_str:
            return "综艺"
        return ""

    def __has_recent_files(self, directory: Path, mtime_threshold: int) -> tuple[bool, list[Path]]:
        """检查目录是否有最近修改的视频文件"""
        recent_files = []
        for file_path in directory.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in self.VIDEO_EXTENSIONS:
                mtime = file_path.stat().st_mtime
                age_days = (time.time() - mtime) / 86400
                if age_days < mtime_threshold:
                    recent_files.append(file_path)
        return bool(recent_files), recent_files

    def process_directory(self, directory: Path):
        """处理单个目录"""
        try:
            media_type = self.__get_media_type(directory)
            if not media_type or media_type not in self._thresholds:
                return

            creation_time = self.__get_creation_time(directory)
            age_days = (time.time() - creation_time) / 86400

            # 检查是否满足任一组阈值配置
            should_archive = False
            matched_threshold = None
            for threshold in self._thresholds[media_type]:
                if age_days >= threshold.creation_days:
                    has_recent, recent_files = self.__has_recent_files(directory, threshold.mtime_days)
                    if not has_recent:
                        should_archive = True
                        matched_threshold = threshold
                        break

            if not should_archive:
                msg = f"[跳过] {media_type}: {directory.name} (不满足任何阈值配置)"
                logger.info(msg)
                self._transfer_messages["skipped"].append(msg)
                return

            # 准备归档
            source_dir = Path(self._source_dir)
            target_dir = Path(self._target_dir)
            relative_path = directory.relative_to(source_dir)
            destination = target_dir / relative_path
            
            try:
                if self._test_mode:
                    msg = f"[测试] {media_type}: {directory.name} -> {destination} (创建时间 {age_days:.1f}天 >= {matched_threshold.creation_days}天)"
                    logger.info(msg)
                    self._transfer_messages["success"].append(msg)
                else:
                    # 创建目标目录
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    # 移动目录
                    shutil.move(str(directory), str(destination))
                    msg = f"[转移] {media_type}: {directory.name} -> {destination} (创建时间 {age_days:.1f}天 >= {matched_threshold.creation_days}天)"
                    logger.info(msg)
                    self._transfer_messages["success"].append(msg)
                    
                    # 保存转移历史
                    history = {
                        "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "media_type": media_type,
                        "media_name": directory.name,
                        "source": str(directory),
                        "target": str(destination),
                        "age_days": round(age_days, 1),
                        "threshold_days": matched_threshold.creation_days
                    }
                    self.__save_history(history)
                    
            except Exception as e:
                msg = f"[错误] 转移失败 {directory.name}: {e}"
                logger.error(msg)
                self._transfer_messages["failed"].append(msg)
                
        except Exception as e:
            logger.error(f"处理目录出错 {directory}: {e}")

    def process_all_directories(self):
        """处理所有目录"""
        if not self._source_dir or not self._target_dir:
            logger.error("未配置源目录或目标目录")
            return
            
        try:
            logger.info("=== 开始处理媒体文件归档 ===")
            
            # 清空之前的消息
            self._transfer_messages = {
                "success": [],
                "skipped": [],
                "failed": []
            }
            
            source_dir = Path(self._source_dir)
            patterns = [
                "电视剧/*/*",
                "动漫/完结动漫/*",
                "电影/*/*",
                "综艺/*"
            ]

            for pattern in patterns:
                directories = list(source_dir.glob(pattern))
                if directories:
                    logger.info(f"\n处理类型: {pattern}")
                    for directory in directories:
                        if directory.is_dir():
                            self.process_directory(directory)
            
            logger.info("\n=== 归档处理完成 ===")
            
            # 发送通知
            self.__send_notification()
            
        except Exception as e:
            logger.error(f"处理过程出错: {str(e)}")
            if self._notify:
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title="【媒体归档处理失败】",
                    text=f"处理过程出错：{str(e)}"
                )

    def __save_history(self, history: dict):
        """保存转移历史记录"""
        try:
            # 获取现有历史记录
            histories = self.get_data('transfer_history') or []
            if not isinstance(histories, list):
                histories = [histories]
                
            # 添加新记录
            histories.append(history)
            
            # 保存更新后的历史记录
            self.save_data('transfer_history', histories)
            logger.info(f"已写入历史记录: {history['media_name']}")
            
        except Exception as e:
            logger.error(f"保存历史记录失败: {str(e)}")

    def __send_notification(self):
        """发送通知"""
        if not self._notify:
            return
        
        try:
            # 构建通知内容
            message_lines = []
            has_content = False
            
            # 添加成功记录
            if self._transfer_messages["success"]:
                has_content = True
                message_lines.append("\n【成功转移】")
                for msg in self._transfer_messages["success"]:
                    message_lines.append(msg)
                
            # 添加跳过记录
            if self._transfer_messages["skipped"]:
                has_content = True
                message_lines.append("\n【跳过处理】")
                for msg in self._transfer_messages["skipped"]:
                    message_lines.append(msg)
                
            # 添加失败记录
            if self._transfer_messages["failed"]:
                has_content = True
                if message_lines:
                    message_lines.append("")
                message_lines.append("【处理失败】")
                for msg in self._transfer_messages["failed"]:
                    message_lines.append(msg)
                    
            # 如果有消息要发送
            if has_content:
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title="【媒体归档处理结果】",
                    text="\n".join(message_lines)
                )
            else:
                logger.info("没有需要通知的内容")
                
        except Exception as e:
            logger.error(f"发送通知时出错: {str(e)}")

    def __update_config(self):
        """更新配置"""
        # 将阈值配置转换为字符串格式
        thresholds_lines = []
        for media_type, thresholds in self._thresholds.items():
            for threshold in thresholds:
                thresholds_lines.append(
                    f"{media_type}#{threshold.creation_days}#{threshold.mtime_days}"
                )
        thresholds_str = "\n".join(thresholds_lines)
        
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": self._onlyonce,
            "test_mode": self._test_mode,
            "notify": self._notify,
            "cron": self._cron,
            "source_dir": self._source_dir,
            "target_dir": self._target_dir,
            "thresholds_str": thresholds_str
        })

    def get_page(self) -> List[dict]:
        """插件页面 - 显示归档处理历史记录"""
        # 获取历史数据
        histories = self.get_data('transfer_history') or []

        return [
            # 统计信息卡片
            {
                'component': 'VRow',
                'content': [
                    # 总处理数量
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 4
                        },
                        'content': [{
                            'component': 'VCard',
                            'props': {
                                'variant': 'tonal'
                            },
                            'content': [{
                                'component': 'VCardText',
                                'content': [{
                                    'component': 'div',
                                    'content': [
                                        {
                                            'component': 'div',
                                            'props': {'class': 'text-subtitle-2'},
                                            'text': '总处理数量'
                                        },
                                        {
                                            'component': 'div',
                                            'props': {'class': 'text-h6'},
                                            'text': str(len(histories))
                                        }
                                    ]
                                }]
                            }]
                        }]
                    }
                ]
            },
            # 转移历史记录表格
            {
                'component': 'VRow',
                'content': [{
                    'component': 'VCol',
                    'props': {
                        'cols': 12
                    },
                    'content': [{
                        'component': 'VCard',
                        'content': [
                            {
                                'component': 'VCardTitle',
                                'content': '转移历史记录'
                            },
                            {
                                'component': 'VCardText',
                                'content': [{
                                    'component': 'VTable',
                                    'props': {
                                        'hover': True
                                    },
                                    'content': [
                                        {
                                            'component': 'thead',
                                            'content': [{
                                                'component': 'tr',
                                                'content': [
                                                    {
                                                        'component': 'th',
                                                        'text': '时间',
                                                        'props': {
                                                            'class': 'text-start ps-4'
                                                        }
                                                    },
                                                    {
                                                        'component': 'th',
                                                        'text': '媒体类型',
                                                        'props': {
                                                            'class': 'text-start ps-4'
                                                        }
                                                    },
                                                    {
                                                        'component': 'th',
                                                        'text': '媒体名称',
                                                        'props': {
                                                            'class': 'text-start ps-4'
                                                        }
                                                    },
                                                    {
                                                        'component': 'th',
                                                        'text': '存在时间(天)',
                                                        'props': {
                                                            'class': 'text-start ps-4'
                                                        }
                                                    }
                                                ]
                                            }]
                                        },
                                        {
                                            'component': 'tbody',
                                            'content': [
                                                {
                                                    'component': 'tr',
                                                    'content': [
                                                        {
                                                            'component': 'td',
                                                            'text': history.get('create_time', '未知')
                                                        },
                                                        {
                                                            'component': 'td',
                                                            'text': history.get('media_type', '未知')
                                                        },
                                                        {
                                                            'component': 'td',
                                                            'text': history.get('media_name', '未知')
                                                        },
                                                        {
                                                            'component': 'td',
                                                            'text': str(history.get('age_days', '未知'))
                                                        }
                                                    ]
                                                } for history in sorted(histories,
                                                                      key=lambda x: datetime.strptime(x.get('create_time', '1970-01-01 00:00:00'),
                                                                                            '%Y-%m-%d %H:%M:%S'),
                                                                      reverse=True)
                                            ]
                                        }
                                    ]
                                }]
                            }
                        ]
                    }]
                }]
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """返回API接口配置"""
        return []

    def stop_service(self):
        """停止插件服务"""
        try:
            if self._scheduler:
                logger.info("正在停止插件服务...")
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
                logger.info("插件服务已停止")
        except Exception as e:
            logger.error(f"停止插件服务失败：{str(e)}") 