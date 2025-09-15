// server.js (updated)
const express = require("express");
const cors = require("cors");
const sqlite3 = require("sqlite3").verbose();
const path = require("path");
const axios = require('axios');
const bcrypt = require("bcryptjs");
const jwt = require("jsonwebtoken");

const app = express();
const PORT = 5000;
const SECRET_KEY = "mysecretkey123"; // đổi thành key mạnh hơn trong production

app.use(cors());
app.use(express.json());

// ====== SQLite init ======
const dbPath = path.resolve(__dirname, "database.db");
const db = new sqlite3.Database(dbPath, (err) => {
  if (err) console.error("❌ SQLite connect error:", err.message);
  else console.log("✅ SQLite connected:", dbPath);
});

// ====== Create / migrate users table if not exists ======
db.serialize(() => {
  // Create table with phone & username (phone unique)
  db.run(`
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      phone TEXT NOT NULL UNIQUE,
      username TEXT NOT NULL UNIQUE,
      password TEXT NOT NULL,
      level INTEGER NOT NULL DEFAULT 1,
      createdAt DATETIME DEFAULT CURRENT_TIMESTAMP
    )
  `);

  db.run(`
    CREATE TABLE IF NOT EXISTS rentals (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      userId TEXT NOT NULL,
      rentalTime INTEGER NOT NULL,
      roomCode TEXT,
      status TEXT NOT NULL DEFAULT 'pending',
      createdAt DATETIME DEFAULT CURRENT_TIMESTAMP,
      expiresAt DATETIME
    )
  `);
});

// ====== Helpers ======
function addMinutes(date, minutes) {
  return new Date(date.getTime() + minutes * 60000);
}

function toSqlDateTime(d) {
  const pad = (n) => (n < 10 ? "0" + n : n);
  return (
    d.getFullYear() +
    "-" +
    pad(d.getMonth() + 1) +
    "-" +
    pad(d.getDate()) +
    " " +
    pad(d.getHours()) +
    ":" +
    pad(d.getMinutes()) +
    ":" +
    pad(d.getSeconds())
  );
}

// ====== Validation helpers (light) ======
function isValidPhone(phone) {
  // VN phone: starts with 0 and total 10-11 digits (adjust if needed)
  return /^0\d{9,10}$/.test(phone);
}

function isValidPassword(pw) {
  return typeof pw === "string" && pw.length >= 6;
}

function isValidUsername(name) {
  // allow letters, numbers, underscore, dash; length 3-30
  return /^[A-Za-z0-9_-]{3,30}$/.test(name);
}

// ====== API Register ======
app.post("/register", async (req, res) => {
  try {
    const { phone, password, username } = req.body;
    if (!phone || !password || !username) return res.status(400).json({ message: "Missing params" });

    if (!isValidPhone(phone)) return res.status(400).json({ message: "Số điện thoại không hợp lệ" });
    if (!isValidPassword(password)) return res.status(400).json({ message: "Mật khẩu quá ngắn (>=6)" });
    if (!isValidUsername(username)) return res.status(400).json({ message: "Username không hợp lệ (3-30 ký tự, chữ/số/_/-)" });

    const hashedPassword = await bcrypt.hash(password, 10);

    db.run(
      `INSERT INTO users (phone, username, password, level) VALUES (?, ?, ?, 1)`,
      [phone, username, hashedPassword],
      function(err) {
        if (err) {
          if (err.message && err.message.includes("UNIQUE")) {
            // detect which field conflicts
            if (err.message.includes("users.phone")) return res.status(400).json({ message: "Số điện thoại đã tồn tại" });
            if (err.message.includes("users.username")) return res.status(400).json({ message: "Username đã tồn tại" });
            return res.status(400).json({ message: "Dữ liệu đã tồn tại" });
          }
          return res.status(500).json({ message: "DB error" });
        }
        db.get(`SELECT id, phone, username, level, createdAt FROM users WHERE id=?`, [this.lastID], (err2, row) => {
          if (err2) return res.status(500).json({ message: "DB error" });
          res.json({ message: "Đăng ký thành công!", user: row });
        });
      }
    );
  } catch (e) {
    console.error(e);
    res.status(500).json({ message: "Server error" });
  }
});

// ====== API Login (by phone) ======
app.post("/login", (req, res) => {
  const { phone, password } = req.body;
  if (!phone || !password) return res.status(400).json({ message: "Missing params" });

  db.get(`SELECT * FROM users WHERE phone=?`, [phone], async (err, user) => {
    if (err) return res.status(500).json({ message: "DB error" });
    if (!user) return res.status(404).json({ message: "Số điện thoại không tồn tại" });

    const match = await bcrypt.compare(password, user.password);
    if (!match) return res.status(400).json({ message: "Mật khẩu không đúng" });

    // tạo token JWT
    const token = jwt.sign({ id: user.id, level: user.level }, SECRET_KEY, { expiresIn: "7d" });

    res.json({
      message: "Đăng nhập thành công!",
      user: { id: user.id, phone: user.phone, username: user.username, level: user.level },
      token
    });
  });
});

// ====== Middleware auth (nếu cần) ======
function authMiddleware(req, res, next) {
  const authHeader = req.headers.authorization;
  if (!authHeader) return res.status(401).json({ message: "No token" });

  const token = authHeader.split(" ")[1];
  try {
    const decoded = jwt.verify(token, SECRET_KEY);
    req.user = decoded;
    next();
  } catch (err) {
    return res.status(401).json({ message: "Token không hợp lệ" });
  }
}

// ====== API Rentals (unchanged) ======
app.post("/rentals", async (req, res) => {
  const { userId, rentalTime } = req.body;
  if (!userId || !rentalTime)
    return res.status(400).json({ message: "Missing params" });

  db.get(
    `SELECT * FROM rentals WHERE userId=? ORDER BY id DESC LIMIT 1`,
    [userId],
    async (err, lastRental) => {
      if (err) return res.status(500).json({ message: "DB error" });

      const expiresAt = toSqlDateTime(addMinutes(new Date(), rentalTime));

      if (!lastRental) {
        db.run(
          `INSERT INTO rentals (userId, rentalTime, status, expiresAt) VALUES (?, ?, 'pending', ?)`,
          [userId, rentalTime, expiresAt],
          function (err) {
            if (err) return res.status(500).json({ message: "DB insert error" });
            const rentalId = this.lastID;

            axios.post("http://127.0.0.1:5001/command", {
              action: "create_room",
              userId,
              rentalTime,
              rentalId,
              extra: null
            })
            .then(() => console.log("✅ Python create_room đã chạy"))
            .catch(e => console.error("❌ Create room tool error:", e.message));

            db.get(`SELECT * FROM rentals WHERE id=?`, [rentalId], (err, row) => {
              if (err) return res.status(500).json({ message: "DB error" });
              res.json({ message: "Tạo room mới", rental: row });
            });
          }
        );
      } else {
        const roomCode = lastRental.roomCode;

        db.run(
          `INSERT INTO rentals (userId, rentalTime, status, roomCode, expiresAt) VALUES (?, ?, 'pending', ?, ?)`,
          [userId, rentalTime, roomCode, expiresAt],
          function (err) {
            if (err) return res.status(500).json({ message: "DB insert error" });
            const newRentalId = this.lastID;

            axios.post("http://127.0.0.1:5001/command", {
              action: "extend_room",
              userId,
              rentalId: newRentalId,
              extra: roomCode
            })
            .then(() => console.log("✅ Python extend_room đã chạy"))
            .catch(e => console.error("❌ Extend tool error:", e.message));

            db.get(`SELECT * FROM rentals WHERE id=?`, [newRentalId], (err, row) => {
              if (err) return res.status(500).json({ message: "DB error" });
              res.json({ message: "Tạo bản ghi mới (extend)", rental: row });
            });
          }
        );
      }
    }
  );
});

app.get("/rentals", (req, res) => {
  db.all(
    "SELECT id, userId, rentalTime, createdAt, roomCode, status FROM rentals ORDER BY datetime(createdAt) DESC",
    [],
    (err, rows) => {
      if (err) return res.status(500).json({ error: err.message });
      res.json(rows);
    }
  );
});

app.get("/rentals/:id", (req, res) => {
  db.get(`SELECT * FROM rentals WHERE id=?`, [req.params.id], (err, row) => {
    if (err) return res.status(500).json({ message: "DB error" });
    if (!row) return res.status(404).json({ message: "Not found" });
    res.json(row);
  });
});

app.post("/rentals/:id/expire", async (req, res) => {
  try {
    const id = req.params.id;
    const rental = await new Promise((resolve, reject) => {
      db.get(`SELECT * FROM rentals WHERE id=?`, [id], (err, row) => {
        if (err) return reject(err);
        resolve(row);
      });
    });
    if (!rental) return res.status(404).json({ message: "Not found" });
    if (rental.status !== "active")
      return res.status(400).json({ message: `Không ở trạng thái active (status=${rental.status})` });

    // Gọi tool đóng room
    try {
      await runAutomation("close_room", [String(rental.roomCode || "")]);
    } catch (e) {
      return res.status(500).json({ message: "Tool automation lỗi khi đóng room", error: e.message });
    }

    await new Promise((resolve) => {
      db.run(`UPDATE rentals SET status='expired' WHERE id=?`, [id], () => resolve());
    });

    db.get(`SELECT * FROM rentals WHERE id=?`, [id], (err, row) => {
      if (err) return res.status(500).json({ message: "DB error" });
      res.json({ message: "Đã thu hồi/đóng room", rental: row });
    });
  } catch (err) {
    console.error(err);
    res.status(500).json({ message: "Server error", error: err.message });
  }
});

app.post("/rentals/:id/extend", (req, res) => {
  const { id } = req.params;
  const { minutes } = req.body;
  if (!minutes || minutes <= 0)
    return res.status(400).json({ message: "minutes > 0" });

  db.get(`SELECT * FROM rentals WHERE id=?`, [id], (err, row) => {
    if (err) return res.status(500).json({ message: "DB error" });
    if (!row) return res.status(404).json({ message: "Not found" });

    const currentExpire = row.expiresAt ? new Date(row.expiresAt) : new Date();
    const newExpire = toSqlDateTime(addMinutes(currentExpire, Number(minutes)));

    db.run(
      `UPDATE rentals SET rentalTime = rentalTime + ?, expiresAt=? WHERE id=?`,
      [minutes, newExpire, id],
      function (err2) {
        if (err2) return res.status(500).json({ message: "DB error" });
        db.get(`SELECT * FROM rentals WHERE id=?`, [id], (err3, row2) => {
          if (err3) return res.status(500).json({ message: "DB error" });
          res.json({ message: "Đã gia hạn", rental: row2 });
        });
      }
    );
  });
});

app.patch("/rentals/:id", (req, res) => {
  const { roomCode, status } = req.body;
  const { id } = req.params;

  db.run(
    `UPDATE rentals SET roomCode=?, status=? WHERE id=?`,
    [roomCode, status || 'active', id],
    function (err) {
      if (err) return res.status(500).json({ message: "DB error" });
      db.get(`SELECT * FROM rentals WHERE id=?`, [id], (err2, row) => {
        if (err2) return res.status(500).json({ message: "DB error" });
        res.json({ message: "Room updated", rental: row });
      });
    }
  );
});


// 🚨 Reset bảng users (chỉ dùng dev/test)
app.post("/reset-users", (req, res) => {
  const secret = req.query.secret;
  if (secret !== "khanh123") {
    return res.status(403).json({ message: "Không có quyền reset" });
  }

  db.run("DROP TABLE IF EXISTS users", (err) => {
    if (err) return res.status(500).json({ message: "DB error", error: err.message });

    db.run(`
      CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phone TEXT NOT NULL UNIQUE,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        level INTEGER NOT NULL DEFAULT 1,
        createdAt DATETIME DEFAULT CURRENT_TIMESTAMP
      )
    `, (err2) => {
      if (err2) return res.status(500).json({ message: "DB error", error: err2.message });
      res.json({ message: "✅ Users table reset thành công!" });
    });
  });
});

// ====== Background auto-expire (mỗi 60s) ======
setInterval(() => {
  const nowSql = toSqlDateTime(new Date());
  db.all(
    `SELECT * FROM rentals WHERE status='active' AND expiresAt IS NOT NULL AND expiresAt <= ?`,
    [nowSql],
    async (err, rows) => {
      if (err || !rows || rows.length === 0) return;
      for (const rental of rows) {
        console.log("⚠️ Quá hạn, auto-close:", rental.id, rental.roomCode);
        try {
          await runAutomation("close_room", [String(rental.roomCode || "")]);
          db.run(`UPDATE rentals SET status='expired' WHERE id=?`, [rental.id]);
        } catch (e) {
          console.error("Auto-close error:", e.message);
        }
      }
    }
  );
}, 60 * 1000);

// ====== Start ======
app.listen(PORT, () => {
  console.log(`✅ Backend running at http://localhost:${PORT}`);
});
