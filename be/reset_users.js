// reset_users.js
const sqlite3 = require("sqlite3").verbose();
const path = require("path");
const bcrypt = require("bcryptjs");

const dbPath = path.resolve(__dirname, "database.db");
const db = new sqlite3.Database(dbPath, (err) => {
  if (err) {
    console.error("❌ SQLite connect error:", err.message);
    process.exit(1);
  }
  console.log("✅ SQLite connected:", dbPath);
});

// Thông tin user seed mặc định
const defaultUser = {
  phone: "0912345678",
  username: "khanh",
  password: "123456",
  level: 1
};

(async () => {
  try {
    // 1️⃣ Drop table cũ
    await new Promise((resolve, reject) => {
      db.run("DROP TABLE IF EXISTS users", (err) => {
        if (err) return reject(err);
        console.log("✅ Users table dropped");
        resolve();
      });
    });

    // 2️⃣ Tạo table mới
    await new Promise((resolve, reject) => {
      db.run(`
        CREATE TABLE users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          phone TEXT NOT NULL UNIQUE,
          username TEXT NOT NULL UNIQUE,
          password TEXT NOT NULL,
          level INTEGER NOT NULL DEFAULT 1,
          createdAt DATETIME DEFAULT CURRENT_TIMESTAMP
        )
      `, (err) => {
        if (err) return reject(err);
        console.log("✅ Users table created with new schema");
        resolve();
      });
    });

    // 3️⃣ Seed user mặc định
    const hashedPassword = await bcrypt.hash(defaultUser.password, 10);

    await new Promise((resolve, reject) => {
      db.run(
        "INSERT INTO users (phone, username, password, level) VALUES (?, ?, ?, ?)",
        [defaultUser.phone, defaultUser.username, hashedPassword, defaultUser.level],
        (err) => {
          if (err) return reject(err);
          console.log("✅ Default user created:", defaultUser.username, "/", defaultUser.phone);
          resolve();
        }
      );
    });

    console.log("🎉 Reset DB & seed user completed!");
    db.close();
  } catch (err) {
    console.error("❌ Error:", err.message);
    db.close();
  }
})();
