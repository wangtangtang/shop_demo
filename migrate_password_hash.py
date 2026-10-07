"""数据库迁移：把 users 表里【明文存储】的老密码批量升级为 PBKDF2 哈希。

背景
----
早期版本密码明文入库（注册 User(password=明文)、登录明文比对），
存在拖库即泄露全部密码的风险。本脚本在不删库、不需要用户改密码的前提下，
把每条明文密码就地哈希化；用户仍可用原密码登录（登录时按哈希校验）。

特性
----
- 幂等：已是 pbkdf2 哈希的记录自动跳过，可重复执行；
- 安全：脚本不打印任何密码明文，只输出升级条数；
- 兼容：即使不跑本脚本，老明文用户登录时也会"透明升级"，
  本脚本用于一次性把全库（含长期不登录的用户）升级到位。

用法（在项目根目录、激活虚拟环境后）
----
    python migrate_password_hash.py
"""
from app import create_app, db
from app.models import User
from app.security import is_hashed


def migrate():
    upgraded = 0
    skipped = 0
    users = User.query.all()
    for u in users:
        if is_hashed(u.password):
            skipped += 1
            continue
        # 老密码此时是明文：直接对其哈希（用户原密码不变，无需通知改密）
        u.set_password(u.password)
        upgraded += 1
    db.session.commit()
    print(f"密码哈希迁移完成：共 {len(users)} 个用户，"
          f"本次升级 {upgraded} 条，已是哈希跳过 {skipped} 条。")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        migrate()
