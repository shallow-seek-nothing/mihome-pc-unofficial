# 逆向报告 02 — 局域网 miIO（APK 证据链）

对象：`com.xiaomi.smarthome` 11.9.705。

> 本文**只采信 APK 内的证据**。上一轮的 v2 实现只作为待验证的假设，
> 每个结论都标了取证位置。

---

## 1. 结论先行

| 问题 | 结论 | 证据 |
|---|---|---|
| APK 是否使用 UDP miIO 局域网 RPC | **是** | `com/xiaomi/miio/OooO00o.java` 提供 `OooO0o0(addr, json, did, token, cb)`；调用方 `com/xiaomi/smarthome/domain/process/ComboProcess.java:242-249` |
| 加解密在哪实现 | **native `libmiio.so`** | `com/xiaomi/miio/JNIBridge.java:21` `System.loadLibrary("miio")` |
| 端口 54321 是否 Java 常量 | **否** | dex 字符串表中无 `54321`、无 `udp://` |
| 加密原语 | **MD5 + AES-CBC** | `libmiio.so` 符号：`_Z3md5PKciPc`、`AES_cbc_encrypt`、`AES_set_encrypt_key` |
| 包头字段 | magic / len / did / stamp | `_Z13init_msg_headPcmjPKct`、`_Z14parse_msg_headPKcRtRmRjPc` |

---

## 2. Java 层的消息模型（权威）

```java
// com/xiaomi/miio/JNIBridge.java:6-18
public static class MiioMsg {
    public long   did;        // ← long，不是 uint32
    public int    stamp;
    public byte[] token;
    public byte[] message;    // 明文 JSON
}
```

native 接口：

```java
static native byte[] encrypt(MiioMsg msg);           // 明文 -> 完整包
static native MiioMsg decrypt(byte[] packet, byte[] token);
static native byte[] hencrypt(MiioMsg msg);          // 握手包
static native MiioMsg hdecrypt(byte[] packet);       // ★ 不需要 token
static native MiioMsg bledecrypt(byte[], byte[]);    // BLE 变体
static native byte[] smencrypt(byte[] data, long did);// secure mode
static native byte[] smencryptpk(byte[] data, long did, byte[] pk);
```

**`hdecrypt` 只要包、不要 token** → 握手包是明文，只用来取出 did/stamp。
这与"发 `21 31 00 20` + 28×FF，回包里读 did/stamp"的行为一致。

---

## 3. native 层符号（`libmiio.so`，36,832 B，未剥离）

C++ 符号demangle 后（`arm64-v8a`）：

| 符号 | 含义 |
|---|---|
| `init_msg_head(char*, unsigned long, unsigned int, char const*, ...)` | 组包头：**did 是 unsigned long，stamp 是 unsigned int** |
| `parse_msg_head(char const*, unsigned short&, unsigned long&, unsigned int&, char*)` | 解包头：ushort + ulong(did) + uint(stamp) |
| `encrypt(unsigned long const&, char const*, unsigned int, char const*, int, char*, int)` | 加密：did, token, stamp, 明文, 长度 |
| `decrypt(char*, int, char const*, char const*, int)` | 解密 |
| `md5(char const*, int, char*)` / `OI_Md5HashBuffer` | 校验和用 MD5 |
| `AES_cbc_encrypt` / `AES_cbc_decrypt4ble_log` / `AES_set_encrypt_key` | AES-CBC |
| `AES_cbc_encrypt_iv` | 显式 IV 的 CBC |
| `crc8_populate_msb` / `_lsb` | BLE 路径的 CRC8 |
| `hex` / `dehex` / `miio_hex` | token 的十六进制处理 |

**这也顺带解释了为什么 dex 里没有端口和加密细节**：全部下沉到 native。

---

## 4. 尚未钉死的两点（诚实标注）

1. **`did` 的实际位宽**：Java 模型是 `long`，native 签名是 `unsigned long`，在
   arm64 上是 8 字节。但已知的 miIO 包结构是 16 字节包头
   (magic2 + len2 + unknown4 + did4 + stamp4)。两者只能有一个成立，
   需要**反汇编 `parse_msg_head`** 或**抓一次真机 LAN 报文**来定。
2. **监听端口**：dex 无字面量，可能来自 native 常量或
   `/v2/home/local_device_list` 下发的 `localip` 配套信息。

---

## 5. 日常控制走不走它？

`domain/process/*` 这个包是**设备配网/添加流程**：

```
ComboProcess      setMiioRouter()   // 下发路由器 SSID/密码
                  getToken()
                  getMiioInfo()     // "miIO.info"
```

也就是说上表证明的是**配网期**的 miIO RPC。日常控制另有路径：
`com/xiaomi/smarthome/newui/card/localcontrol/OooO00o.java` 里有
`/miotspec/prop/get`、`/miotspec/prop/set` 以及 "local OTU" 日志，
那是卡片侧的本地控制入口 —— **待逆**。

---

## 6. 对 v3 的处置

* `mihome_pc/xiaomi/miio.py` 目前是 **v2 血统**，仅作假设保留，
  **不接入 UI/CLI 默认流程**。
* 待第 4 节两点钉死后，才按 APK 证据重写并接入。
* 在那之前，所有设备操作走云端（已验证可用）。

---

## 7. 新增：日常本地控制（「中枢」通道）的架构（第 32 轮）

这一条是本轮新查明的，取代此前「只知道是独立服务 + mbedTLS」的模糊结论。

### 7.1 分层

```
应用进程                                   独立服务进程（:localcontrol）
  LocalServiceManager extends ILocalControl.Stub   ← AIDL
        │  bindService / AIDL 调用                   │
        └──────────────────────────────────────────►  mf2.OooO0oO()
                                                      └─ CentralClientPhone / CentralClient
                                                           ├─ MdnsClient        （mDNS 发现）
                                                           └─ native: libcentral_client_interface.so
                                                                      └─ libcentralclient.so（mbedTLS）
```

* 入口：com/xiaomi/smarthome/localcontrol/common/LocalServiceManager.java:23
* 真正的客户端：com/xiaomi/miot/centralclientsdk/CentralClientPhone.java
* 宿主：_m_j/mf2.java:305

### 7.2 发现：mDNS

```java
// CentralClientPhone / MdnsClient
_miot-central._tcp.        // 服务类型
central_service            // TXT 记录字段
profile
```

中枢上报的 profile 字段（CentralProfile.java）：
did, groupId, role, linkType(eth=0|wifi…), rssi, version, suitIDM, suitMQTT, isOnline, fixedRole, priority

### 7.3 传输与鉴权

* libcentralclient.so 内是 **mbedTLS**（ADH-AES128-GCM-SHA256 等密码套件字符串）。
* 支持**证书请求/下发**：CentralClientPhone.replyCertificateRequest(String) —— 即双向证书 TLS。
* 启动参数：CentralClientPhone.start(context, uid, ownerUid, homeId, didPath, callbacks)，
  其中 did 从 <didPath> 文件读取（首行 did,a 形式，两段相同才采用）。

### 7.4 控制调用

```java
CentralClientPhone.callAPI(String name, String payload,
                           CentralClient.CallAPICallbacks cb, Object ctx, int i)
CentralClientPhone.registerBroadcast(String, BroadcastHandler, Object)   // 状态订阅
CentralClientPhone.changeHomeId(uid, homeId)
```

CentralClient 里可见的 API 字面量只有 refresh，版本 v0.2.9，
其余方法均为 native。

### 7.5 结论与边界（诚实标注）

* **架构层面已逆清**：mDNS 发现 → TLS 双向证书 → callAPI(name, payload)。
* **消息级协议未逆清**：具体 API 名、payload schema、证书签发流程都下沉在
  libcentral_client_interface.so / libcentralclient.so 里，需要
  **反汇编 arm64** 才能继续。本机没有 objdump/Ghidra。
* 因此 v3 客户端**不实现这条通道**，所有操作走云端（已验证可用）。
  若将来要补：需要 capstone/ghidra 之一 + 一台中枢网关做联调。
