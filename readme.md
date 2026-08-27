# student15267 workspace notes

## 基本路径

个人工作目录：

```bash
/home/gl/科研/organized_code_files/student15267
```

登录服务器并进入个人目录：

```bash
ssh organized_code_files
cd student15267
```

## 启动 Codex

启动：

```bash
./codex
```

检查状态：

```bash
./codex doctor
```

## 退出

退出 Codex：

```text
Ctrl+C
```

退出服务器 shell：

```bash
exit
```

或者：

```text
Ctrl+D
```

## 常用文件操作

查看当前目录：

```bash
pwd
ls -la
```

创建子目录：

```bash
mkdir -p data
```

上传本地文件到服务器：

```bash
scp local_file organized_code_files:~/科研/organized_code_files/student15267/
```

下载服务器文件到本地：

```bash
scp organized_code_files:~/科研/organized_code_files/student15267/remote_file ./
```

## 注意事项

- 尽量只在 `~/科研/organized_code_files/student15267` 下工作。
- 不要在共享账号的其他目录里随便改文件。
- `./codex` 是这个目录里的快捷入口。
