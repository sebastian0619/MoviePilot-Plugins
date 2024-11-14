"""
BangumiArchive插件
用于自动归档完结/连载番剧
"""
from typing import Any, Dict, List, Tuple, Optional
from app.core.config import settings
from app.core.event import eventmanager, Event, EventType
from app.core.context import Context, MediaInfo
from app.plugins import _PluginBase
from app.schemas.types import MediaType
from app.log import logger
from app.helper.module import ModuleHelper
from datetime import datetime
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

class BangumiArchive(_PluginBase):
    # 插件基础信息
    plugin_name = "连载番剧归档"
    plugin_desc = "自动检测连载目录中的番剧，识别完结情况并归档到完结目录"
    plugin_version = "1.3"
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
    mediachain = None
    _scheduler = None
    _last_check_time = {}  # 用于记录每个媒体的最后检查时间
    # 用于收集通知信息
    _transfer_messages = {
        "airing_to_end": [],    # 连载->完结
        "end_to_airing": [],    # 完结->连载
        "failed": []           # 处理失败
    }

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
        发送汇总通知
        """
        if not self._notify:
            return
            
        try:
            messages = []
            
            # 添加连载->完结的信息
            if self._transfer_messages["airing_to_end"]:
                messages.append("【完结归档】")
                messages.extend(self._transfer_messages["airing_to_end"])
                messages.append("")  # 空行分隔
                
            # 添加完结->连载的信息
            if self._transfer_messages["end_to_airing"]:
                messages.append("【恢复连载】")
                messages.extend(self._transfer_messages["end_to_airing"])
                messages.append("")  # 空行分隔
                
            # 添加失败信息
            if self._transfer_messages["failed"]:
                messages.append("【处理失败】")
                messages.extend(self._transfer_messages["failed"])
                
            # 如果有消息才发送通知
            if messages:
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title="【番剧归档处理结果】",
                    text="\n".join(messages)
                )
                
            # 清空消息列表
            self._transfer_messages = {
                "airing_to_end": [],
                "end_to_airing": [],
                "failed": []
            }
            
        except Exception as e:
            logger.error(f"发送通知失败：{str(e)}")

    def check_status(self, tmdb_id: int, max_retries: int = 3) -> tuple:
        """
        检查剧集状态，支持重试
        """
        for retry in range(max_retries):
            try:
                mediainfo = self.mediachain.recognize_media(tmdbid=tmdb_id, mtype=MediaType.TV)
                if mediainfo:
                    return self.__parse_status(mediainfo)
                if retry < max_retries - 1:
                    logger.warning(f"第 {retry + 1} 次获取媒体信息失败，准备重试...")
                    time.sleep(1)  # 重试前等待1秒
            except Exception as e:
                if retry < max_retries - 1:
                    logger.error(f"第 {retry + 1} 次获取媒体信息出错: {str(e)}，准备重试...")
                    time.sleep(1)
                else:
                    logger.error(f"最终获取媒体信息失败: {str(e)}")
        return False, "无法识别媒体信息"

    def __parse_status(self, mediainfo: MediaInfo) -> tuple:
        """
        解析媒体状态
        """
        # 打印基本信息
        logger.info(f"媒体信息: {mediainfo.title} ({mediainfo.year})")
        logger.info(f"当前状态: {mediainfo.status}")
        
        # 检查状态
        status = mediainfo.status.lower() if mediainfo.status else ''
        if status in self.END_STATUS:
            logger.info(f"媒体已完结，完结状态: {status}")
            return True, status
        
        # 检查最后播出日期
        if mediainfo.last_air_date:
            try:
                last_date = datetime.strptime(mediainfo.last_air_date, '%Y-%m-%d')
                days_diff = (datetime.now() - last_date).days
                
                # 计算具体时间
                years = days_diff // 365
                remaining_days = days_diff % 365
                months = remaining_days // 30
                days = remaining_days % 30
                
                time_desc = []
                if years > 0:
                    time_desc.append(f"{years}年")
                if months > 0:
                    time_desc.append(f"{months}个月")
                if days > 0:
                    time_desc.append(f"{days}天")
                    
                time_str = "".join(time_desc)
                
                logger.info(f"最后播出日期: {mediainfo.last_air_date}")
                logger.info(f"距今已过: {time_str}")
                logger.info(f"完结判定阈值: {self._end_after_days}天")
                
                # 使用配置的天数判断
                if days_diff > self._end_after_days:
                    logger.info(f"超过{self._end_after_days}天未更新，视为完结")
                    return True, f"最后播出超过{self._end_after_days}天 ({mediainfo.last_air_date})"
                else:
                    logger.info(f"未超过{self._end_after_days}天，视为连载中")
                    
            except ValueError as e:
                logger.error(f"日期解析错误: {str(e)}")
        else:
            logger.info("无最后播出日期信息")
                
        return False, status

    def __save_history(self, source: str, target: str, media_name: str, 
                      tmdb_id: int, old_status: str, new_status: str, 
                      transfer_type: str):
        """
        保存历史记录
        """
        try:
            history = self.get_data('history') or {}
            history[str(tmdb_id)] = {
                'time': datetime.now(tz=pytz.timezone(settings.TZ)).strftime('%Y-%m-%d %H:%M:%S'),
                'source': source,
                'target': target,
                'media_name': media_name,
                'old_status': old_status,
                'new_status': new_status,
                'transfer_type': transfer_type
            }
            self.save_data('history', history)
            logger.info(f"保存历史记录: {media_name}")
        except Exception as e:
            logger.error(f"保存历史记录失败: {str(e)}")

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

    def __transfer_media(self, source: str, target: str, tmdb_id: int, 
                        old_status: str, new_status: str):
        """
        转移媒体并记录
        """
        try:
            media_name = os.path.basename(source)
            if self._test_mode:
                logger.info(f"测试模式 - 需要移动: {source} -> {target}")
            else:
                # 移动文件
                shutil.move(source, target)
                logger.info(f"已移动: {source} -> {target}")
                
            # 确定移动类型和原因
            if new_status.lower() in [status.lower() for status in self.END_STATUS]:
                # 连载->完结
                transfer_type = "airing_to_end"
                message = f"《{media_name}》: 状态变更为完结 ({new_status})"
                self._transfer_messages["airing_to_end"].append(message)
            else:
                # 完结->连载
                transfer_type = "end_to_airing"
                message = f"《{media_name}》: 已恢复连载"
                self._transfer_messages["end_to_airing"].append(message)
            
            # 保存历史
            self.__save_history(
                source=source,
                target=target,
                media_name=media_name,
                tmdb_id=tmdb_id,
                old_status=old_status,
                new_status=new_status,
                transfer_type=transfer_type
            )
                
        except Exception as e:
            logger.error(f"转移失败: {str(e)}")

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
        """
        处理目录
        """
        try:
            for item in os.listdir(source_dir):
                item_path = os.path.normpath(os.path.join(source_dir, item))
                
                # 检查路径是否已处理
                if processed_paths is not None and item_path in processed_paths:
                    logger.debug(f"跳过已处理的路径: {item_path}")
                    continue
                    
                if not os.path.isdir(item_path):
                    continue
                
                logger.debug(f"处理目录: {item_path}")
                
                # 获取TMDB ID
                tmdb_id = self.__get_tmdb_id(item_path)
                if not tmdb_id:
                    logger.warning(f"无法获取TMDB ID: {item}")
                    # 记录处理失败信息
                    self._transfer_messages["failed"].append(
                        f"《{item}》: 无法获取TMDB ID"
                    )
                    continue
                    
                # 检查状态
                is_ended, status = self.check_status(tmdb_id)
                
                # 如果状态为 unknown 或包含"无法识别"，标记为失败
                if status.lower() == "unknown" or "无法识别" in status:
                    logger.warning(f"无法识别媒体状态: {item} (状态: {status})")
                    # 记录处理失败信息
                    self._transfer_messages["failed"].append(
                        f"《{item}》: 无法识别媒体状态 ({status})"
                    )
                    # 记录已处理的路径
                    if processed_paths is not None:
                        processed_paths.add(item_path)
                    continue
                    
                if check_ended != is_ended:
                    # 状态不符合要求
                    logger.debug(f"保持不变的剧集: {item} (状态: {status})")
                    # 记录已处理的路径
                    if processed_paths is not None:
                        processed_paths.add(item_path)
                    continue
                    
                # 检查是否需要移动
                if not self.__should_transfer(tmdb_id, status):
                    logger.info(f"跳过最近已处理的媒体: {item}")
                    # 记录已处理的路径
                    if processed_paths is not None:
                        processed_paths.add(item_path)
                    continue
                    
                # 获取历史状态
                last_history = self.__get_last_history(tmdb_id)
                last_status = last_history.get("new_status") if last_history else None
                
                # 移动目录
                target_path = os.path.join(target_dir, item)
                self.__transfer_media(
                    source=item_path,
                    target=target_path,
                    tmdb_id=tmdb_id,
                    old_status=last_status or "unknown",
                    new_status=status
                )
                
                # 记录已处理的路径
                if processed_paths is not None:
                    processed_paths.add(item_path)
                
        except Exception as e:
            logger.error(f"处理目录出错: {str(e)}")

    def __get_tmdb_id(self, dir_path: str) -> int:
        """
        获取目录的TMDB ID
        优先从NFO文件获取,其次从文件名解析
        """
        try:
            # 尝试从tvshow.nfo文件获取
            nfo_path = os.path.join(dir_path, "tvshow.nfo")
            if os.path.exists(nfo_path):
                tmdbid = self.__get_tmdbid_from_nfo(nfo_path)
                if tmdbid:
                    logger.debug(f"从tvshow.nfo获取到TMDB ID: {tmdbid}")
                    return tmdbid
            
            # 查找目录下的其他nfo文件
            for file in os.listdir(dir_path):
                if file.endswith(".nfo"):
                    nfo_path = os.path.join(dir_path, file)
                    try:
                        tmdbid = self.__get_tmdbid_from_nfo(nfo_path)
                        if tmdbid:
                            logger.debug(f"从{file}获取到TMDB ID: {tmdbid}")
                            return tmdbid
                    except Exception as e:
                        logger.debug(f"读取NFO文件 {file} 失败: {str(e)}")
                        continue
            
            # 从文件名解析
            meta_info = self.meta_helper.get_media_info(
                title=os.path.basename(dir_path),
                mtype=MediaType.TV
            )
            if meta_info and meta_info.tmdb_id:
                logger.debug(f"从文件名解析到TMDB ID: {meta_info.tmdb_id}")
                return meta_info.tmdb_id
            
        except Exception as e:
            logger.debug(f"获取TMDB ID失败: {str(e)}")
        
        return None

    @staticmethod
    def __get_tmdbid_from_nfo(file_path: Path):
        """
        从nfo文件中获取信息
        :param file_path:
        :return: tmdbid
        """
        if not file_path:
            return None
        xpaths = [
            "uniqueid[@type='Tmdb']",
            "uniqueid[@type='tmdb']",
            "uniqueid[@type='TMDB']",
            "tmdbid"
        ]
        try:
            reader = NfoReader(file_path)
            for xpath in xpaths:
                tmdbid = reader.get_element_value(xpath)
                if tmdbid:
                    return tmdbid
        except Exception as err:
            logger.warn(f"从nfo文件中获取tmdbid失败：{str(err)}")
        return None

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

            # 处理��个目录对
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
                    text=f"错误信息：{str(e)}"
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
        插件页面 - 显示历史记录
        """
        histories = self.get_data('history')
        if not histories:
            return [{
                'component': 'div',
                'text': '暂无归档记录',
                'props': {
                    'class': 'text-center'
                }
            }]

        if not isinstance(histories, list):
            histories = [histories]

        # 按时间倒序排序
        histories = sorted(histories, 
                         key=lambda x: datetime.strptime(x.get("create_time"), "%Y-%m-%d %H:%M:%S"), 
                         reverse=True)

        contents = []
        for history in histories:
            contents.append({
                'component': 'tr',
                'content': [
                    {
                        'component': 'td',
                        'text': history.get("create_time")
                    },
                    {
                        'component': 'td',
                        'text': history.get("media_name")
                    },
                    {
                        'component': 'td',
                        'text': "完结归档" if history.get("transfer_type") == "airing->end" else "恢复连载"
                    },
                    {
                        'component': 'td',
                        'text': f'{history.get("old_status")} -> {history.get("new_status")}'
                    }
                ]
            })

        return [{
            'component': 'VRow',
            'content': [{
                'component': 'VCol',
                'props': {
                    'cols': 12
                },
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
                            'content': contents
                        }
                    ]
                }]
            }]
        }]

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
