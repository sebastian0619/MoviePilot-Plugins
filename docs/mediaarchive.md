# 媒体文件归档 (MediaArchive)

自动将满足条件的媒体文件从源目录归档到目标目录。

## 功能特点

- 支持多种媒体类型（电影、完结动漫、电视剧、综艺）
- 根据媒体类型设置不同的归档阈值
- 检查文件夹创建时间和最近修改时间
- 支持定时自动运行
- 支持手动执行
- 支持测试模式
- 支持通知功能
- 保存转移历史记录

## 配置说明

### 基础配置
- 启用插件: 开启/关闭插件功能
- 立即运行一次: 立即执行一次归档任务
- 测试模式: 不实际移动文件,仅显示将要执行的操作
- 发送通知: 是否发送通知消息
- 执行周期: 设置自动运行的时间间隔(Cron表达式)
- 源目录: 设置媒体文件的源目录
- 目标目录: 设置归档的目标目录

### 媒体类型阈值配置
- 电影: 创建时间20天，修改时间20天
- 完结动漫: 创建时间100天，修改时间45天
- 电视剧: 创建时间10天，修改时间90天
- 综艺: 创建时间1天，修改时间1天

### 目录结构要求
源目录下需要按以下结构组织媒体文件：
```
源目录/
  ├── 电影/
  │   └── */
  ├── 动漫/
  │   └── 完结动漫/
  ├── 电视剧/
  │   └── */
  └── 综艺/
```

## 归档条件

媒体文件夹需要同时满足以下条件才会被归档：
1. 文件夹创建时间超过设定阈值
2. 文件夹内所有视频文件的最后修改时间都超过设定阈值

## 注意事项

1. 确保源目录和目标目录都有正确的读写权限
2. 建议先使用测试模式运行确认
3. 移动操作不可撤销,请谨慎使用
4. 支持的视频文件格式:
   - mp4, mkv, avi, ts, m2ts
   - mov, wmv, iso, m4v
   - mpg, mpeg, rm, rmvb

## 版本历史

### v1.0
- 首次发布
- 支持基础归档功能
- 支持多种媒体类型
- 支持测试模式
- 支持定时任务
- 支持通知功能
- 支持历史记录 