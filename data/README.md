# 本地象棋资料库

本目录存放运行时下载的大文件，不提交到 Git。

- 数据来源：Chinese Chess Practical Dataset（CCPD）
- 下载地址：https://github.com/Yvonne761/Chinese-Chess-Practical-Dataset/archive/refs/heads/main.zip
- 项目主页：https://github.com/Yvonne761/Chinese-Chess-Practical-Dataset
- 数据许可：CC BY 4.0
- 本地原始目录：`data/ccpd_source/`
- 生成索引：`data/opening_book.json.gz`

推荐直接执行一键安装：

```powershell
py -3 setup_opening_book.py
```

如果已经手工下载并解压到 `data/ccpd_source/`，只生成索引：

```powershell
py -3 import_ccpd.py
```

生成的紧凑索引供直播助手只读查询。没有索引时，程序自动使用内置的保守开局规则，不影响自动下棋。
