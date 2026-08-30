| Service | LIVE (34dev) | CLONE (testco) | Why |
|---|---|---|---|
| Hermes gateway | 127.0.0.1:8642 | 127.0.0.1:**8652** | +10, same loopback. Clone must never answer on the live port. |
| Jarvis app | 127.0.0.1:8700 | 127.0.0.1:**8710** | +10. |
| Gmail webhook | 127.0.0.1:18790 | not started | Needs a tunnel + Pub/Sub; out of scope for a boot test. |
| cloudflared | 3 quick tunnels | none | The clone is reached over loopback only. |

A real new droplet would use the SAME ports as the live box (8642/8700) because it is a
different machine. The +10 offset is purely an artifact of rehearsing on one host.
