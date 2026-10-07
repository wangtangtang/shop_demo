// ========== 登录 / 注册 ==========
async function doLogin(e) {
    e.preventDefault();
    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value;
    const errEl = document.getElementById("login-error");
    errEl.textContent = "";

    const { status, data } = await api("/api/login", {
        method: "POST",
        body: { username, password }
    });

    if (status === 200 && data.code === 0) {
        setAuth(data.data.token, data.data.username, data.data.is_admin);
        toast(data.data.is_admin ? "管理员登录成功" : "登录成功", "success");
        // 管理员登录后默认进后台，普通顾客回首页
        setTimeout(() => location.href = data.data.is_admin ? "/admin" : "/", 600);
    } else {
        errEl.textContent = data.msg || "登录失败";
    }
    return false;
}

async function doRegister(e) {
    e.preventDefault();
    const username = document.getElementById("reg-username").value.trim();
    const password = document.getElementById("reg-password").value;
    const errEl = document.getElementById("register-error");
    errEl.textContent = "";

    const { status, data } = await api("/api/register", {
        method: "POST",
        body: { username, password }
    });

    if (status === 200 && data.code === 0) {
        toast("注册成功，正在登录...", "success");
        // 注册后自动登录
        const loginResp = await api("/api/login", {
            method: "POST",
            body: { username, password }
        });
        if (loginResp.data.code === 0) {
            const d = loginResp.data.data;
            setAuth(d.token, d.username, d.is_admin);
            setTimeout(() => location.href = "/", 600);
        }
    } else {
        errEl.textContent = data.msg || "注册失败";
    }
    return false;
}
