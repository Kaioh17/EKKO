//! Desktop shell for ekko. Finds or starts the Python backend and hands the
//! UI its URL and per-launch token over IPC, so no secret is ever compiled
//! into the web bundle or put in a URL.
//!
//! Backend resolution, first hit wins:
//! 1. `EKKO_BACKEND_URL` + `EKKO_API_TOKEN` in the environment (point the
//!    app at a backend you started yourself).
//! 2. `runtime.json` in the app data dir, written by a backend that is
//!    already running (the always-on logon task), if it answers /api/health.
//! 3. Spawn one: the bundled `ekko-backend` sidecar in release builds, or
//!    `python -m backend` from the repo in debug builds.
//!
//! A backend this app spawned is asked to stop via POST /api/shutdown on
//! exit (clean: its listener stops too), and watches this process's PID in
//! case the app dies first. No stdin pipe: on Windows a thread blocked
//! reading one deadlocks the backend's native DLL loads.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
#[cfg(test)]
use std::sync::Arc;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};

#[derive(Clone, serde::Serialize, serde::Deserialize, Debug, PartialEq)]
pub struct BackendInfo {
    pub url: String,
    pub token: String,
}

struct Backend {
    info: BackendInfo,
    child: Mutex<Option<Child>>,
}

pub fn free_port() -> std::io::Result<u16> {
    Ok(TcpListener::bind(("127.0.0.1", 0))?.local_addr()?.port())
}

pub fn new_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).expect("OS random number generator unavailable");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// One loopback request with the token; true on a 2xx. Plain std HTTP:
/// pulling in an HTTP client for this isn't worth it.
fn call(info: &BackendInfo, method: &str, path: &str) -> bool {
    let Some(addr) = info.url.strip_prefix("http://").and_then(|a| a.parse::<SocketAddr>().ok()) else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(500)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Ekko-Token: {}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        info.token
    );
    let mut head = [0u8; 12];
    stream.write_all(request.as_bytes()).is_ok()
        && stream.read_exact(&mut head).is_ok()
        && head.starts_with(b"HTTP/1.1 2")
}

fn healthy(info: &BackendInfo) -> bool {
    call(info, "GET", "/api/health")
}

fn wait_healthy(info: &BackendInfo, timeout: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if healthy(info) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    false
}

fn from_runtime_file(data_dir: &Path) -> Option<BackendInfo> {
    #[derive(serde::Deserialize)]
    struct Runtime {
        port: u16,
        token: String,
        #[serde(default)]
        version: String,
    }
    let raw = std::fs::read_to_string(data_dir.join("runtime.json")).ok()?;
    let rt: Runtime = serde_json::from_str(&raw).ok()?;
    let info = BackendInfo { url: format!("http://127.0.0.1:{}", rt.port), token: rt.token };
    if !healthy(&info) {
        return None;
    }
    if rt.version != env!("CARGO_PKG_VERSION") {
        // A backend from a different version is running (e.g. the
        // sign-in task's backend, still on the old files right after an
        // update replaced them). Never attach to it -- ask it to stop
        // and let the caller start a fresh, matching one instead.
        eprintln!(
            "ekko: found a running backend on version {:?} (this app is {}), asking it to stop",
            rt.version,
            env!("CARGO_PKG_VERSION")
        );
        call(&info, "POST", "/api/shutdown");
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline && healthy(&info) {
            std::thread::sleep(Duration::from_millis(200));
        }
        return None;
    }
    Some(info)
}

/// How to start the backend, and the data dir it will use.
fn backend_command(app: &tauri::AppHandle) -> Result<(Command, PathBuf), String> {
    let sidecar = app
        .path()
        .resource_dir()
        .map_err(|e| e.to_string())?
        .join("ekko-backend")
        .join(if cfg!(windows) { "ekko-backend.exe" } else { "ekko-backend" });
    if sidecar.is_file() {
        let data_dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
        let mut cmd = Command::new(sidecar);
        cmd.env("EKKO_DATA_DIR", &data_dir);
        return Ok((cmd, data_dir));
    }
    if cfg!(debug_assertions) {
        // Dev: the repo's own venv; data stays in the repo, as it always has.
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
        let python = [repo.join("pvenv/Scripts/python.exe"), repo.join("venv/bin/python")]
            .into_iter()
            .find(|p| p.is_file())
            .ok_or("no pvenv/ or venv/ in the repo; create one (see readme.md)")?;
        let mut cmd = Command::new(python);
        cmd.args(["-m", "backend"]).current_dir(&repo);
        return Ok((cmd, repo));
    }
    Err("the bundled ekko-backend is missing; reinstall ekko".into())
}

fn start_backend(app: &tauri::AppHandle) -> Result<Backend, String> {
    if let (Ok(url), Ok(token)) = (std::env::var("EKKO_BACKEND_URL"), std::env::var("EKKO_API_TOKEN")) {
        return Ok(Backend { info: BackendInfo { url, token }, child: Mutex::new(None) });
    }
    let (mut cmd, data_dir) = backend_command(app)?;
    if let Some(info) = from_runtime_file(&data_dir) {
        return Ok(Backend { info, child: Mutex::new(None) });
    }
    let port = free_port().map_err(|e| e.to_string())?;
    let info = BackendInfo { url: format!("http://127.0.0.1:{port}"), token: new_token() };
    cmd.env("EKKO_PORT", port.to_string())
        .env("EKKO_API_TOKEN", &info.token)
        .env("EKKO_PARENT_PID", std::process::id().to_string())
        .stdin(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        if !cfg!(debug_assertions) {
            cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        }
    }
    let child = cmd.spawn().map_err(|e| format!("could not start the backend: {e}"))?;
    Ok(Backend { info, child: Mutex::new(Some(child)) })
}

/// The UI's only way to learn where the backend is. Waits until it answers.
#[tauri::command]
async fn backend_info(state: tauri::State<'_, Backend>) -> Result<BackendInfo, String> {
    let info = state.info.clone();
    let probe = info.clone();
    let up = tauri::async_runtime::spawn_blocking(move || wait_healthy(&probe, Duration::from_secs(90)))
        .await
        .map_err(|e| e.to_string())?;
    if up {
        Ok(info)
    } else {
        Err("The ekko backend did not start. Check the logs in the data folder.".into())
    }
}

fn stop_backend(backend: &Backend) {
    let Some(mut child) = backend.child.lock().unwrap().take() else { return };
    call(&backend.info, "POST", "/api/shutdown"); // clean: stops the listener too
    let deadline = Instant::now() + Duration::from_secs(8);
    while Instant::now() < deadline {
        if let Ok(Some(_)) = child.try_wait() {
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .setup(|app| {
            let backend = start_backend(app.handle()).unwrap_or_else(|e| {
                eprintln!("ekko: {e}");
                Backend { info: BackendInfo { url: String::new(), token: String::new() }, child: Mutex::new(None) }
            });
            app.manage(backend);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_info])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                stop_backend(&app.state::<Backend>());
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn free_port_is_bindable() {
        let port = free_port().unwrap();
        assert!(port > 0);
        TcpListener::bind(("127.0.0.1", port)).unwrap();
    }

    #[test]
    fn tokens_are_64_hex_and_unique() {
        let (a, b) = (new_token(), new_token());
        assert_eq!(a.len(), 64);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b);
    }

    #[test]
    fn unreachable_backend_is_unhealthy() {
        let port = free_port().unwrap();
        let info = BackendInfo { url: format!("http://127.0.0.1:{port}"), token: "x".into() };
        assert!(!healthy(&info));
        assert!(from_runtime_file(Path::new("/definitely/missing")).is_none());
    }

    /// A minimal HTTP/1.1 server: 200 OK to anything, records each
    /// request's path so a test can assert whether /api/shutdown was hit.
    /// Runs until `stop` is sent.
    fn fake_backend() -> (BackendInfo, Arc<Mutex<Vec<String>>>, std::sync::mpsc::Sender<()>) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        listener.set_nonblocking(true).unwrap();
        let calls = Arc::new(Mutex::new(Vec::new()));
        let calls_bg = calls.clone();
        let (tx, rx) = std::sync::mpsc::channel::<()>();
        std::thread::spawn(move || {
            while rx.try_recv().is_err() {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
                        let mut buf = [0u8; 512];
                        let n = stream.read(&mut buf).unwrap_or(0);
                        if let Some(line) = String::from_utf8_lossy(&buf[..n]).lines().next() {
                            calls_bg.lock().unwrap().push(line.to_string());
                        }
                        let _ = stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n");
                    }
                    Err(_) => std::thread::sleep(Duration::from_millis(20)),
                }
            }
        });
        (BackendInfo { url: format!("http://127.0.0.1:{port}"), token: "tok".into() }, calls, tx)
    }

    fn write_runtime_json(dir: &Path, port: &str, version: &str) {
        std::fs::create_dir_all(dir).unwrap();
        std::fs::write(
            dir.join("runtime.json"),
            format!(r#"{{"port": {port}, "token": "tok", "version": "{version}"}}"#),
        )
        .unwrap();
    }

    #[test]
    fn matching_version_attaches_without_asking_it_to_stop() {
        let (info, calls, stop) = fake_backend();
        let port = info.url.rsplit(':').next().unwrap().to_string();
        let dir = std::env::temp_dir().join(format!("ekko-test-match-{}", std::process::id()));
        write_runtime_json(&dir, &port, env!("CARGO_PKG_VERSION"));

        assert_eq!(from_runtime_file(&dir), Some(info));
        assert!(!calls.lock().unwrap().iter().any(|l| l.contains("/api/shutdown")));

        let _ = stop.send(());
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn mismatched_version_is_asked_to_stop_and_never_attached() {
        let (info, calls, stop) = fake_backend();
        let port = info.url.rsplit(':').next().unwrap().to_string();
        let dir = std::env::temp_dir().join(format!("ekko-test-mismatch-{}", std::process::id()));
        write_runtime_json(&dir, &port, "0.0.1-not-the-app-version");

        assert!(from_runtime_file(&dir).is_none());
        assert!(calls.lock().unwrap().iter().any(|l| l.contains("/api/shutdown")));

        let _ = stop.send(());
        std::fs::remove_dir_all(&dir).ok();
    }
}
