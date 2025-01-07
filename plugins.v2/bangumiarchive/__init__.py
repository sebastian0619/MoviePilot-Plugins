"""
BangumiArchive插件
用于自动归档完结/连载番剧
"""
from typing import Any, Dict, List, Tuple, Optional
from app.core.config import settings
from app.core.meta import MetaBase
from app.core.event import eventmanager, Event, EventType
from app.core.context import Context, MediaInfo
from app.plugins import _PluginBase
from app.schemas.types import MediaType
from app.log import logger
from app.helper.module import ModuleHelper
from datetime import datetime
from app.chain.tmdb import TmdbChain
import os
import shutil
from pathlib import Path
from app.chain.media import MediaChain
from app.helper.nfo import NfoReader
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.schemas import NotificationType
import pytz
from datetime import timedelta
import time
import re
import traceback

class BangumiArchive(_PluginBase):
    # 插件基础信息
    plugin_name = "连载番剧归档"
    plugin_desc = "自动检测连载目录中的番剧，识别完结情况并归档到完结目录"
    plugin_version = "1.7"
    plugin_icon = "emby.png"
    plugin_author = "Sebastian0619"
    author_url = "https://github.com/sebastian0619"
    plugin_config_prefix = "bangumiarchive_"
    plugin_order = 21
    auth_level = 1

    # 配置信息
    _enabled = False
    _onlyonce = False
    _cron = None
    _paths = None
    _test_mode = False
    _notify = False
    _bidirectional = False
    _end_after_days = 730  # 默认730天(2年)

    # 状态常量定义
    END_STATUS = {"Ended", "Canceled"}
    
    # 状态映射
    STATUS_MAPPING = {
        "unknown": "未知",
        "Ended": "已完结",
        "Canceled": "已取消",
        "Returning Series": "连载中"
    }
    
    # 在类中初始化
    meta_helper = None
    mediachain = None
    _scheduler = None
    _last_check_time = {}  # 用于记录每个媒体的最后检查时间
    # 用于收集通知信息
    _transfer_messages = {
        "airing_to_end": [],    # 连载->完结
        "end_to_airing": [],    # 完结->连载
        "failed": []           # 处理失败
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 初始化 tmdbchain 属性
        self.tmdbchain = TmdbChain()  # 使用导入的 TmdbChain 类进行实例化

    def init_plugin(self, config: dict = None):
        """
        插件初始化
        """
        try:
            self.meta_helper = ModuleHelper()
            self.mediachain = MediaChain()
            
            if config:
                self._enabled = config.get("enabled")
                self._onlyonce = config.get("onlyonce")
                self._cron = config.get("cron")
                self._paths = config.get("paths")
                self._test_mode = config.get("test_mode")
                self._notify = config.get("notify")
                self._bidirectional = config.get("bidirectional")
                # 添加新配置项，如果未配置则使用默认值
                self._end_after_days = int(config.get("end_after_days", 730))
                
                # 如果开启立即运行
                if self._enabled and self._onlyonce:
                    logger.info(f"番剧归档服务启动，立即运行一次...")
                    # 行一次任务
                    self.check_and_move()
                    # 关闭一次性开关
                    self._onlyonce = False
                    self.__update_config()
                
                # 周期运行
                if self._enabled and self._cron:
                    try:
                        self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                        self._scheduler.add_job(func=self.check_and_move,
                                              trigger=CronTrigger.from_crontab(self._cron),
                                              name="番剧归档")
                        if self._scheduler.get_jobs():
                            self._scheduler.print_jobs()
                            self._scheduler.start()
                            logger.info(f"周期任务已启动，执行周期：{self._cron}")
                    except Exception as err:
                        logger.error(f"周期任务启动失败：{str(err)}")
            
            # 验证历史记录格式
            self.__verify_history_format()
        except Exception as e:
            logger.error(f"插件初始化失败: {str(e)}")

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """配置表单"""
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
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
                                    'md': 4
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
                                    'md': 4
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
                                    'md': 4
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
                                    'cols': 12,
                                    'md': 4
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
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 4
                        },
                        'content': [
                            {
                                'component': 'VTextField',
                                'props': {
                                    'model': 'days_to_complete',
                                    'label': '视为完结天数',
                                    'placeholder': '输入天数，例如：730'
                                }
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
                                            'placeholder': '5位cron表达式，留空自动'
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
                                            'model': 'paths',
                                            'label': '目录配置',
                                            'placeholder': '连载目录:完结目录\n例如：/media/anime/airing:/media/anime/ended',
                                            'rows': 2
                                        }
                                    }
                                ]
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
            'bidirectional': False,
            'cron': '5 1 * * *',
            'paths': '',
            'end_after_days': 730
        }

    def __send_notification(self):
        """
        发送通知
        """
        if not self._notify:
            return
        
        try:
            # 构建通知内容
            message_lines = []
            has_content = False
            
            # 添加连载->完结的记录
            if self._transfer_messages["airing_to_end"]:
                has_content = True
                message_lines.append("\n【连载->完结】")
                for msg in self._transfer_messages["airing_to_end"]:
                    message_lines.append(msg)
                
            # 添加完结->连载的记录
            if self._transfer_messages["end_to_airing"]:
                has_content = True
                message_lines.append("\n【完结->连载】")
                for msg in self._transfer_messages["end_to_airing"]:
                    message_lines.append(msg)
                
            # 添加处理失败的记录
            if self._transfer_messages["failed"]:
                has_content = True
                if message_lines:  # 如果前面有其他消息，添加一个空行
                    message_lines.append("")
                message_lines.append("【处理失败】")
                for msg in self._transfer_messages["failed"]:
                    message_lines.append(msg)
                    
            # 如果有消息要发送
            if has_content:
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title="【番剧归档处理结果】",
                    text="\n".join(message_lines)
                )
            else:
                logger.info("没有需要通知的内容")
                
        except Exception as e:
            logger.error(f"发送通知时出错: {str(e)}")
            logger.error(f"错误详情: {traceback.format_exc()}")
            # 错误通知也使用 post_message
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title="【番剧归档处理失败】",
                text=f"处理过程出错：{str(e)}"
            )

    def check_status(self, tmdb_id: int, path: str) -> Tuple[bool, str]:
        """
        检查媒体状态
        """
        MAX_RETRIES = 3
        RETRY_DELAY = 5
        
        for attempt in range(MAX_RETRIES):
            try:
                # 使用 mediachain 通过路径识别媒体信息
                context = self.mediachain.recognize_by_path(path)
                if not context or not context.media_info:
                    if attempt < MAX_RETRIES - 1:
                        logger.warning(f"第 {attempt + 1} 次获取媒体信息失败，{RETRY_DELAY}秒后重试...")
                        time.sleep(RETRY_DELAY)
                        continue
                    else:
                        logger.error(f"在 {MAX_RETRIES} 次尝试后仍无法获取媒体信息: {path}")
                        return False, "unknown"
                
                media_info = context.media_info
                
                # 验证TMDB ID是否匹配
                if media_info.tmdb_id != tmdb_id:
                    logger.error(f"TMDB ID不匹配: 期望 {tmdb_id}, 实际 {media_info.tmdb_id}")
                    return False, "unknown"
                
                # 获取关键信息
                name = media_info.title
                status = media_info.status
                first_air_date = media_info.air_date
                last_air_date = media_info.last_air_date
                
                if not status or not last_air_date:
                    logger.error(f"媒体信息不完整: {path}")
                    return False, "unknown"
                    
                # 计算距今天数
                try:
                    last_date = datetime.strptime(last_air_date, '%Y-%m-%d')
                    today = datetime.now()
                    days_diff = (today - last_date).days
                    
                    # 只输出一次日志
                    logger.info(f"媒体信息: {name} ({first_air_date[:4] if first_air_date else '未知'})")
                    logger.info(f"当前状态: {status}")
                    logger.info(f"最后播出日期: {last_air_date}")
                    logger.info(f"距今已过: {days_diff}天")
                    logger.info(f"完结判定阈值: {self._end_after_days}天")
                    
                    if days_diff > self._end_after_days:
                        logger.info(f"超过{self._end_after_days}天未更新，视为完结")
                        return True, f"最后播出超过{self._end_after_days}天 ({last_air_date})"
                    else:
                        logger.info(f"未超过{self._end_after_days}天，视为连载中")
                        return False, status
                    
                except ValueError as e:
                    logger.error(f"日期解析错误: {str(e)}")
                    return False, status
                    
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"第 {attempt + 1} 次请求失败: {str(e)}，{RETRY_DELAY}秒后重试...")
                    time.sleep(RETRY_DELAY)
                    continue
                else:
                    logger.error(f"在 {MAX_RETRIES} 次尝试后仍然失败: {str(e)}")
                    logger.error(f"错误详情: {traceback.format_exc()}")
                    return False, "unknown"

    def __transfer_media(self, source: str, target: str, tmdb_id: int, old_status: str, new_status: str):
        """
        移动媒体文件并记录历史
        """
        try:
            if self._test_mode:
                logger.info(f"测试模式 - 需要移动: {source} -> {target}")
            else:
                # 移动文件
                shutil.move(source, target)
                logger.info(f"已移动: {source} -> {target}")
                
                # 保存转移历史
                history = {
                    "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "media_name": os.path.basename(source),
                    "tmdb_id": tmdb_id,
                    "source": source,
                    "target": target,
                    "old_status": old_status,
                    "new_status": new_status,
                    "transfer_type": "airing_to_end" if old_status == "Returning Series" else "end_to_airing"
                }
                
                # 获取现有历史记录
                histories = self.get_data('transfer_history') or []
                if not isinstance(histories, list):
                    histories = [histories]
                    
                # 添加新记录
                histories.append(history)
                
                # 保存更新后的历史记录
                self.save_data('transfer_history', histories)
                logger.info(f"已写入历史记录: {os.path.basename(source)} - {history['transfer_type']}")
                
                # 添加到通知消息
                transfer_type = history['transfer_type']
                operation_type = (
                    "完结归档" if (old_status == "Returning Series" and new_status == "Ended") or (old_status == "unknown" and new_status == "Ended")
                    else "恢复连载" if (old_status == "Ended" and new_status == "Returning Series") or (old_status == "unknown" and new_status == "Returning Series")
                    else f"状态变更 ({self.STATUS_MAPPING.get(old_status, old_status)} -> {self.STATUS_MAPPING.get(new_status, new_status)})"
                )
                self._transfer_messages[transfer_type].append(
                    f"《{os.path.basename(source)}》: {operation_type}"
                )
                
        except Exception as e:
            logger.error(f"移动媒体文件失败: {str(e)}")
            # 记录失败历史
            failed_history = {
                "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "media_name": os.path.basename(source),
                "error_msg": str(e)
            }
            
            # 获取现有失败历史
            failed_histories = self.get_data('failed_history') or []
            if not isinstance(failed_histories, list):
                failed_histories = [failed_histories]
                
            # 添加新的失败记录
            failed_histories.append(failed_history)
            
            # 保存失败历史
            self.save_data('failed_history', failed_histories)
            logger.info(f"已写入失败记录: {os.path.basename(source)} - {str(e)}")
            
            # 添加到通知消息
            self._transfer_messages["failed"].append(
                f"《{os.path.basename(source)}》: 移动失败 - {str(e)}"
            )

    def __save_failed_history(self, media_name: str, media_path: str, error_msg: str):
        """
        保存失败历史记录
        """
        try:
            history = {
                "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "media_name": media_name,
                "media_path": media_path,
                "error_msg": error_msg
            }
            
            # 获取现有历史记录
            histories = self.get_data('failed_history') or []
            if not isinstance(histories, list):
                histories = [histories]
                
            # 添加新记录
            histories.append(history)
            
            # 保存更新后的历史记录
            self.save_data('failed_history', histories)
            
        except Exception as e:
            logger.error(f"保存失败历史记录失败: {str(e)}")

    def __get_last_history(self, tmdb_id: int) -> Optional[dict]:
        """
        获取最近一次移动记录
        """
        try:
            histories = self.get_data("history") or []
            if not isinstance(histories, list):
                histories = [histories]
                
            # 按时间倒序过滤指定tmdb_id的记录
            media_histories = [h for h in histories if h.get("tmdb_id") == tmdb_id]
            if media_histories:
                return sorted(media_histories, 
                            key=lambda x: datetime.strptime(x.get("create_time"), "%Y-%m-%d %H:%M:%S"),
                            reverse=True)[0]
        except Exception as e:
            logger.error(f"获取历史记录失败: {str(e)}")
        return None

    def __should_transfer(self, tmdb_id: int, new_status: str) -> bool:
        """
        判断是否需要移动
        """
        # 获取最近的移动记录
        last_history = self.__get_last_history(tmdb_id)
        
        # 获取上次检查时间
        last_check = self._last_check_time.get(tmdb_id)
        now = datetime.now()
        
        # 如果最近24小时内检查过，跳过
        if last_check and (now - last_check).total_seconds() < 24 * 3600:
            return False
            
        # 更新检查时间
        self._last_check_time[tmdb_id] = now
        
        # 如果没有历史记录，需要移动
        if not last_history:
            return True
            
        # 如果状态发生变化，需要移动
        if last_history.get("new_status", "").lower() != new_status.lower():
            return True
            
        return False

    def __process_directory(self, source_dir: str, target_dir: str, check_ended: bool, processed_paths: set):
        """处理目录"""
        try:
            for item in os.listdir(source_dir):
                item_path = os.path.normpath(os.path.join(source_dir, item))
                
                # 跳过已处理的路径
                if item_path in processed_paths:
                    continue
                    
                if not os.path.isdir(item_path):
                    continue

                # 获取媒体信息
                tmdb_id = self.__get_tmdb_id(item_path)
                if not tmdb_id:
                    self._transfer_messages["failed"].append(f"《{item}》: 无法获取TMDB ID")
                    continue

                # 一次性获取所有媒体信息
                media_info = self._get_media_info(tmdb_id)
                if not media_info:
                    self._transfer_messages["failed"].append(f"《{item}》: 无法识别媒体信息")
                    continue

                # 检查是否需要处理
                status = media_info.get("status")
                last_air_date = media_info.get("last_air_date")
                
                # 检查完结状态
                is_ended = self.__check_if_ended(status, last_air_date)
                
                # 根据检查类型决定是否需要移动
                if (check_ended and is_ended) or (not check_ended and not is_ended):
                    if self.__need_transfer(tmdb_id, status):
                        target_path = os.path.join(target_dir, item)
                        self.__transfer_media(
                            source=item_path,
                            target=target_path,
                            tmdb_id=tmdb_id,
                            old_status=self.__get_last_status(tmdb_id),
                            new_status=status
                        )
                
                processed_paths.add(item_path)

        except Exception as e:
            logger.error(f"处理目录出错: {str(e)}")

    def __get_tmdb_id(self, path: str) -> Optional[int]:
        """
        获取TMDB ID
        """
        try:
            # 1. 首先尝试从 nfo 文件获取
            nfo_path = os.path.join(path, "tvshow.nfo")
            if os.path.exists(nfo_path):
                with open(nfo_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # 尝试匹配 uniqueid 标签
                    match = re.search(r'<uniqueid type="tmdb">(\d+)</uniqueid>', content)
                    if match:
                        tmdb_id = int(match.group(1))
                        logger.debug(f"从tvshow.nfo获取到TMDB ID: {tmdb_id}")
                        return tmdb_id

            # 2. 如果 nfo 文件不存在或无法获取ID，尝试从目录名称识别
            media_name = os.path.basename(path)
            # 移除年份
            match = re.search(r"(.+?)(?:\s+\(\d{4}\))?$", media_name)
            if match:
                media_name = match.group(1).strip()
                year = None
                
                # 尝试提取年份
                year_match = re.search(r"\((\d{4})\)", os.path.basename(path))
                if year_match:
                    year = year_match.group(1)
                
                # 创建 MetaBase 对象，设置完整的元数据
                meta = MetaBase(title=media_name)
                meta.type = MediaType.TV
                meta.name = media_name  # 设置 name 属性
                if year:
                    meta.year = year
                
                # 使用 mediachain 的 recognize_by_meta 方法
                media_info = self.mediachain.recognize_by_meta(meta)
                if media_info:
                    tmdb_id = media_info.tmdb_id
                    if tmdb_id:
                        logger.debug(f"从目录名称识别到TMDB ID: {tmdb_id}")
                        return tmdb_id
                
                # 如果第一次识别失败，尝试使用 mediachain 的 recognize_by_path 方法
                if not media_info:
                    logger.info(f"尝试使用路径识别: {path}")
                    context = self.mediachain.recognize_by_path(path)
                    if context and context.media_info:
                        tmdb_id = context.media_info.tmdb_id
                        if tmdb_id:
                            logger.debug(f"从路径识别到TMDB ID: {tmdb_id}")
                            return tmdb_id
                
            logger.warning(f"无法识别媒体: {media_name}")
            return None
            
        except Exception as e:
            logger.error(f"获取TMDB ID失败: {str(e)}")
            return None

    def __get_last_status(self, tmdb_id: int) -> str:
        """
        获取最近一次的状态
        """
        try:
            # 获取最近的移动记录
            last_history = self.__get_last_history(tmdb_id)
            if last_history:
                return last_history.get("new_status", "unknown")
            return "unknown"
        except Exception as e:
            logger.error(f"获取最近状态失败: {str(e)}")
            return "unknown"

    def _get_media_info(self, tmdb_id: int, path: str = None, retry_count: int = 3) -> Optional[Dict]:
        """
        获取媒体详细信息
        @param tmdb_id: TMDB ID
        @param path: 媒体路径
        @param retry_count: 重试次数
        @return: 媒体信息字典
        """
        for i in range(retry_count):
            try:
                media_info = None
                
                # 方案1: 如果提供了路径,优先使用路径识别
                if path:
                    context = self.mediachain.recognize_by_path(path)
                    if context and context.media_info:
                        media_info = context.media_info
                
                # 方案2: 如果路径识别失败或未提供路径,使用TMDB API
                if not media_info:
                    try:
                        from app.modules.themoviedb.tmdbapi import TmdbApi
                        tmdb_api = TmdbApi()
                        media_info = tmdb_api.get_info(mtype=MediaType.TV, tmdbid=tmdb_id)
                    except Exception as e:
                        logger.error(f"TMDB API调用失败: {str(e)}")
                        if i < retry_count - 1:
                            continue
                
                if media_info:
                    # 记录关键信息
                    name = media_info.title if hasattr(media_info, 'title') else media_info.get('name')
                    year = (media_info.air_date if hasattr(media_info, 'air_date') else media_info.get('first_air_date', ''))[:4]
                    status = media_info.status if hasattr(media_info, 'status') else media_info.get('status')
                    last_air_date = media_info.last_air_date if hasattr(media_info, 'last_air_date') else media_info.get('last_air_date')
                    
                    logger.info(f"媒体信息: {name} ({year})")
                    logger.info(f"当前状态: {status}")
                    
                    return media_info
                
                if i < retry_count - 1:
                    logger.warning(f"第 {i + 1} 次获取媒体信息失败，准备重试...")
                    time.sleep(5)  # 添加重试延迟
                    
            except Exception as e:
                logger.error(f"获取媒体信息出错: {str(e)}")
                logger.error(f"错误详情: {traceback.format_exc()}")
                if i < retry_count - 1:
                    time.sleep(5)  # 添加重试延迟
                    continue
                    
        return None

    def __check_if_ended(self, status: str, last_air_date: str) -> bool:
        """检查是否已完结"""
        if status in self.END_STATUS:
            return True
            
        if not last_air_date:
            return False
            
        # 计算距今天数
        days_passed = (datetime.now() - datetime.strptime(last_air_date, "%Y-%m-%d")).days
        logger.info(f"距今已过: {days_passed}天")
        logger.info(f"完结判定阈值: {self._end_after_days}天")
        
        if days_passed > self._end_after_days:
            logger.info("超过判定天数，视为已完结")
            return True
            
        logger.info("未超过判定天数，视为连载中")
        return False

    def check_and_move(self):
        """
        检查并移动文件
        """
        if not self._paths:
            logger.error("未配置目录映射")
            return
            
        try:
            # 清空之前的通知信息
            self._transfer_messages = {
                "airing_to_end": [],
                "end_to_airing": [],
                "failed": []
            }
            
            # 解析目录映射
            path_list = []
            processed_paths = set()  # 记录已处理的路径
            
            for path_pair in self._paths.splitlines():
                if not path_pair.strip():
                    continue
                source, target = path_pair.split(":")
                source = os.path.normpath(source.strip())  # 标准化路径
                target = os.path.normpath(target.strip())
                if source and target:
                    path_list.append((source, target))

            # 处理个目录对
            for source_dir, target_dir in path_list:
                if not os.path.exists(source_dir) or not os.path.exists(target_dir):
                    logger.error(f"目录不存在: {source_dir} 或 {target_dir}")
                    continue
                    
                logger.info("开始检查连载->完结...")
                # 先处理连载->完结
                self.__process_directory(
                    source_dir=source_dir,
                    target_dir=target_dir,
                    check_ended=True,
                    processed_paths=processed_paths
                )
                
                # 如果开启双向监控，再处理完结->连载
                if self._bidirectional:
                    logger.info("开始检查完结->连载...")
                    self.__process_directory(
                        source_dir=target_dir,
                        target_dir=source_dir,
                        check_ended=False,
                        processed_paths=processed_paths
                    )
                    
            # 处理完成后发送通知
            self.__send_notification()
                    
        except Exception as e:
            logger.error(f"检查过程出错: {str(e)}")
            if self._notify:
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title="【番剧归档处理失败】",
                    text=f"检查过程出错：{str(e)}"
                )

    def get_state(self) -> bool:
        return self._enabled
    
    def get_command(self) -> List[Dict[str, Any]]:
        return []

    def stop_service(self):
        pass

    def __update_config(self):
        """
        更新配置
        """
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": self._onlyonce,
            "test_mode": self._test_mode,
            "notify": self._notify,
            "bidirectional": self._bidirectional,
            "cron": self._cron,
            "paths": self._paths,
            "end_after_days": self._end_after_days  # 添加新配置项
        })

    def get_page(self) -> List[dict]:
        """
        插件页面 - 显示归档处理历史记录
        """
        # 获取历史数据
        transfer_histories = self.get_data('transfer_history') or []
        failed_histories = self.get_data('failed_history') or []

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
                            'md': 3
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
                                            'text': str(len(transfer_histories))
                                        }
                                    ]
                                }]
                            }]
                        }]
                    },
                    # 成功转移数量
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 3
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
                                            'text': '成功转移数量'
                                        },
                                        {
                                            'component': 'div',
                                            'props': {'class': 'text-h6'},
                                            'text': str(len([h for h in transfer_histories if h.get("transfer_type") == "airing_to_end"]))
                                        }
                                    ]
                                }]
                            }]
                        }]
                    },
                    # 恢复连载数量
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 3
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
                                            'text': '恢复连载数量'
                                        },
                                        {
                                            'component': 'div',
                                            'props': {'class': 'text-h6'},
                                            'text': str(len([h for h in transfer_histories if h.get("transfer_type") == "end_to_airing"]))
                                        }
                                    ]
                                }]
                            }]
                        }]
                    },
                    # 失败数量
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 3
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
                                            'text': '失败数量'
                                        },
                                        {
                                            'component': 'div',
                                            'props': {'class': 'text-h6'},
                                            'text': str(len(failed_histories))
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
                        'props': {
                            'variant': 'tonal'
                        },
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
                                                        'text': '媒体名称',
                                                        'props': {
                                                            'class': 'text-start ps-4'
                                                        }
                                                    },
                                                    {
                                                        'component': 'th',
                                                        'text': '操作类型',
                                                        'props': {
                                                            'class': 'text-start ps-4'
                                                        }
                                                    },
                                                    {
                                                        'component': 'th',
                                                        'text': '状态变化',
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
                                                            'text': history.get('media_name', '未知')
                                                        },
                                                        {
                                                            'component': 'td',
                                                            'text': (lambda old, new: "完结归档" if (old == "Returning Series" and new == "Ended") or (old == "unknown" and new == "Ended")
                                                                   else "恢复连载" if (old == "Ended" and new == "Returning Series") or (old == "unknown" and new == "Returning Series")
                                                                   else f"状态变更 ({self.STATUS_MAPPING.get(old, old)} -> {self.STATUS_MAPPING.get(new, new)})")(history.get('old_status', 'unknown'), history.get('new_status', 'unknown'))
                                                        },
                                                        {
                                                            'component': 'td',
                                                            'text': f"{self.STATUS_MAPPING.get(history.get('old_status', 'unknown'), '未知')} -> {self.STATUS_MAPPING.get(history.get('new_status', 'unknown'), '未知')}"
                                                        }
                                                    ]
                                                } for history in sorted(transfer_histories,
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
            },
            # 失败记录表格
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
                                'content': '失败记录'
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
                                                        'text': '媒体名称',
                                                        'props': {
                                                            'class': 'text-start ps-4'
                                                        }
                                                    },
                                                    {
                                                        'component': 'th',
                                                        'text': '错误信息',
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
                                                            'text': history.get('media_name', '未知')
                                                        },
                                                        {
                                                            'component': 'td',
                                                            'text': history.get('error_msg', '未知错误')
                                                        }
                                                    ]
                                                } for history in sorted(failed_histories,
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
        """
        返回API接口配置
        """
        return []
    def __get_transfer_reason(self, old_status: str, new_status: str) -> str:
        """
        获取转移原因描述
        """
        if new_status in self.END_STATUS:
            return f"状态变更为完结 ({new_status})"
        elif old_status in self.END_STATUS and new_status == "Returning Series":
            return "已恢复连载"
        else:
            return f"状态从 {old_status} 变更为 {new_status}"

    def process_directory(self, dir_path: str):
        """处理单个目录"""
        # 获取TMDB ID
        tmdb_id = self._get_tmdb_id(dir_path)
        if not tmdb_id:
            return
            
        # 一次性获取所有需要的信息
        media_info = self._get_media_info(tmdb_id)
        if not media_info:
            logger.debug(f"保持不变的剧集: {os.path.basename(dir_path)} (状态: 无法识别媒体信息)")
            return
            
        # 使用获取到的信息进行处理
        status = media_info.get("status")
        last_air_date = media_info.get("last_air_date")
        
        # 后续的状态判断和处理逻辑...

    def __verify_history_format(self):
        """
        验证历史记录格式
        """
        try:
            # 验证转移历史
            transfer_histories = self.get_data('transfer_history') or []
            if transfer_histories:
                for history in transfer_histories:
                    required_fields = ['create_time', 'media_name', 'transfer_type', 'old_status', 'new_status']
                    missing_fields = [field for field in required_fields if field not in history]
                    if missing_fields:
                        logger.warning(f"转移历史记录缺少字段: {missing_fields}")
                    
            # 验证失败历史
            failed_histories = self.get_data('failed_history') or []
            if failed_histories:
                for history in failed_histories:
                    required_fields = ['create_time', 'media_name', 'error_msg']
                    missing_fields = [field for field in required_fields if field not in history]
                    if missing_fields:
                        logger.warning(f"失败历史记录缺少字段: {missing_fields}")
                    
        except Exception as e:
            logger.error(f"验证历史记录格式失败: {str(e)}")

    def __need_transfer(self, tmdb_id: int, new_status: str) -> bool:
        """
        判断是否需要转移
        """
        # 获取最近的移动记录
        last_history = self.__get_last_history(tmdb_id)
        
        # 获取上次检查时间
        last_check = self._last_check_time.get(tmdb_id)
        now = datetime.now()
        
        # 如果最近24小时内检查过，跳过
        if last_check and (now - last_check).total_seconds() < 24 * 3600:
            return False
        
        # 更新检查时间
        self._last_check_time[tmdb_id] = now
        
        # 如果没有历史记录，需要移动
        if not last_history:
            return True
        
        # 如果状态发生变化，需要移动
        if last_history.get("new_status", "").lower() != new_status.lower():
            return True
        
        return False

class TransferHistory:
    def __init__(self):
        self.source_path: str  # 源路径
        self.target_path: str  # 目标路径
        self.media_name: str   # 媒体名称
        self.tmdb_id: int     # TMDB ID
        self.old_status: str  # 原状态
        self.new_status: str  # 新状态
        self.transfer_type: str # 移动类型 (end->airing 或 airing->end)
        self.create_time: datetime # 移动时间
