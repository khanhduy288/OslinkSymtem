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
const WORKER_API = "https://06e11082d82b.ngrok-free.app";

app.use(cors());
app.use(express.json());

// ====== SQLite init ======
const dbPath = path.resolve(__dirname, "database.db");
const db = new sqlite3.Database(dbPath, (err) => {
  if (err) console.error("❌ SQLite connect error:", err.message);
  else console.log("✅ SQLite connected:", dbPath);
});

// ====== Create / migrate users table if not exists ======
// ====== Create / migrate users & rentals table if not exists ======
db.serialize(() => {
  // Users table
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

  // Rentals table (thêm 3 cột mới để support gia hạn)
  db.run(`
    CREATE TABLE IF NOT EXISTS rentals (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      userId TEXT NOT NULL,
      rentalTime INTEGER NOT NULL,
      roomCode TEXT,
      status TEXT NOT NULL DEFAULT 'pending',
      createdAt DATETIME DEFAULT CURRENT_TIMESTAMP,
      expiresAt DATETIME,
      requestedExtendMonths INTEGER,
      extendTimeInMinutes INTEGER,
      tabs INTEGER
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

function authMiddleware(req, res, next) {
  const authHeader = req.headers.authorization;
  if (!authHeader) return res.status(401).json({ message: "No token" });

  const token = authHeader.split(" ")[1];
  try {
    const decoded = jwt.verify(token, SECRET_KEY);
    req.user = decoded; // chứa { id, level }
    next();
  } catch (err) {
    return res.status(401).json({ message: "Token không hợp lệ" });
  }
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

// GET tất cả users (chỉ admin)
app.get("/admin/users", authMiddleware, (req, res) => {
  if (req.user.level < 10) return res.status(403).json({ message: "Không đủ quyền" });

  db.all("SELECT id, phone, username, level, createdAt FROM users ORDER BY id ASC", [], (err, rows) => {
    if (err) return res.status(500).json({ message: err.message });
    res.json(rows);
  });
});

// ====== API Rentals (unchanged) ======
// ====== API Rentals (FE gửi username, tabs, months) ======
app.post("/rentals", authMiddleware, async (req, res) => {
  const { username, tabs, months } = req.body;
  if (!username || !tabs || !months)
    return res.status(400).json({ message: "Missing params" });

  try {
    const rentalTimeInMinutes = months * 30 * 24 * 60; // 1 tháng = 30 ngày
    const now = new Date();

    const rentalsToInsert = [];
    for (let i = 0; i < tabs; i++) {
      const expiresAt = toSqlDateTime(addMinutes(now, rentalTimeInMinutes));
      rentalsToInsert.push({ username, rentalTime: rentalTimeInMinutes, expiresAt });
    }

    // Insert n bản ghi
    db.serialize(() => {
      const stmt = db.prepare(
        `INSERT INTO rentals (userId, rentalTime, status, createdAt, expiresAt, tabs) VALUES (?, ?, 'pending', ?, ?, ?)`
      );

      rentalsToInsert.forEach(r => {
        stmt.run([req.user.id, r.rentalTime, toSqlDateTime(now), r.expiresAt, 1]);
      });

      stmt.finalize((err) => {
        if (err) return res.status(500).json({ message: "DB insert error", error: err.message });
        res.json({ message: `Tạo ${tabs} bản ghi thành công!` });
      });
    });

  } catch (err) {
    console.error(err);
    res.status(500).json({ message: "Server error" });
  }
});


// API cho user thường
app.get("/rentals", authMiddleware, (req, res) => {
  const userId = req.user.id;
  db.all(
    `SELECT rentals.*, users.username 
     FROM rentals 
     JOIN users ON rentals.userId = users.id 
     WHERE rentals.userId=? 
     ORDER BY datetime(rentals.createdAt) DESC`,
    [userId],
    (err, rows) => {
      if (err) return res.status(500).json({ error: err.message });
      res.json(rows);
    }
  );
});

// API cho admin
app.get("/admin/rentals", authMiddleware, (req, res) => {
  if (req.user.level < 10) return res.status(403).json({ message: "Không đủ quyền" });
  db.all(
    `SELECT rentals.*, users.username 
     FROM rentals 
     JOIN users ON rentals.userId = users.id 
     ORDER BY datetime(rentals.createdAt) DESC`,
    [],
    (err, rows) => {
      if (err) return res.status(500).json({ message: err.message });
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

app.patch("/admin/rentals/:id", authMiddleware, (req, res) => {
  if (req.user.level < 10) return res.status(403).json({ message: "Không đủ quyền" });
  const { status } = req.body;
  const { id } = req.params;
  db.run("UPDATE rentals SET status=? WHERE id=?", [status, id], function(err){
    if(err) return res.status(500).json({ message: err.message });
    res.json({ message: "Cập nhật thành công" });
  });
});

app.get("/admin/stats", authMiddleware, (req,res)=>{
  if(req.user.level < 10) return res.status(403).json({message:"Không đủ quyền"});
  db.get("SELECT COUNT(*) as totalUsers FROM users", [], (err,u)=> {
    db.get("SELECT COUNT(*) as totalRentals FROM rentals", [], (err,r)=>{
      db.get("SELECT SUM(rentalTime*pricePerTab) as revenue FROM rentals WHERE status='active'", [], (err,s)=>{
        res.json({
          totalUsers: u.totalUsers,
          totalRentals: r.totalRentals,
          revenue: s.revenue || 0
        });
      });
    });
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

app.patch("/rentals/:id", authMiddleware, (req, res) => {
  const { id } = req.params;
  const { status, roomCode, rentalTime } = req.body;

  db.get(`SELECT * FROM rentals WHERE id=?`, [id], (err, rental) => {
    if (err) return res.status(500).json({ message: "DB error" });
    if (!rental) return res.status(404).json({ message: "Rental not found" });

    const newStatus = status ?? rental.status;
    const newRentalTime = rentalTime ?? rental.rentalTime;
    const newRoomCode = roomCode ?? rental.roomCode;

    // Nếu status được cập nhật sang active và chưa có roomCode
    if (newStatus === "active" && !newRoomCode) {
      // Check user có rental active khác chưa
      db.get(
        `SELECT * FROM rentals WHERE userId=? AND status='active' AND id!=?`,
        [rental.userId, id],
        (err2, activeRental) => {
          if (err2) return res.status(500).json({ message: "DB error" });

          if (activeRental) {
            // Đã có rental active, gọi worker extend_room
            console.log(`[INFO] User ${rental.userId} đã có rental active, gửi extend_room`);
            axios.post(`${WORKER_API}/command`, {
              action: "extend_room",
              userId: rental.userId,
              rentalId: id,
              rentalTime: newRentalTime,
              roomCode: activeRental.roomCode
            }).then(() => {
              db.run(
                `UPDATE rentals SET status='active', rentalTime=? WHERE id=?`,
                [newRentalTime, id],
                () => res.json({ message: "Đã gửi extend_room cho worker", rentalId: id })
              );
            }).catch((e) => res.status(500).json({ message: "Worker API error", error: e.message }));

          } else {
            // Chưa có rental active, tạo room mới
            axios.post(`${WORKER_API}/command`, {
              action: "create_room",
              userId: rental.userId,
              rentalId: id,
              rentalTime: newRentalTime
            }).then(() => {
              db.run(
                `UPDATE rentals SET status='active', rentalTime=? WHERE id=?`,
                [newRentalTime, id],
                () => res.json({ message: "Đã gửi create_room cho worker", rentalId: id })
              );
            }).catch((e) => res.status(500).json({ message: "Worker API error", error: e.message }));
          }
        }
      );

    } else {
      // Cập nhật thông thường, không liên quan worker
      db.run(
        `UPDATE rentals SET status=?, rentalTime=?, roomCode=? WHERE id=?`,
        [newStatus, newRentalTime, newRoomCode, id],
        function (err3) {
          if (err3) return res.status(500).json({ message: "DB error" });
          db.get(`SELECT * FROM rentals WHERE id=?`, [id], (err4, updated) => {
            if (err4) return res.status(500).json({ message: "DB error" });
            res.json({ message: "Rental updated", rental: updated });
          });
        }
      );
    }
  });
});

app.delete("/rentals/:id", (req, res) => {
  const { id } = req.params;

  db.run("DELETE FROM rentals WHERE id = ?", [id], function (err) {
    if (err) return res.status(500).json({ message: "DB error" });
    if (this.changes === 0) return res.status(404).json({ message: "Rental not found" });

    res.json({ message: "Rental deleted successfully" });
  });
});

// Chỉ tạo tạm, xóa sau khi xong
app.post('/admin/set-level', async (req, res) => {
  const { userId, level } = req.body;

  // Có thể check token admin nếu muốn
  const sql = `UPDATE users SET level = ? WHERE id = ?`;
  db.run(sql, [level, userId], function(err) {
    if (err) return res.status(500).json({ message: err.message });
    res.json({ message: `Cập nhật level user ${userId} thành ${level}` });
  });
});

// 🟢 User gửi yêu cầu gia hạn
app.post("/rentals/:id/request-extend", authMiddleware, (req, res) => {
  const { id } = req.params;
  const { requestedExtendMonths, extendTimeInMinutes, tabs } = req.body;

  db.run(
    `UPDATE rentals 
     SET status = 'pending_extend', 
         requestedExtendMonths = ?, 
         extendTimeInMinutes = ?, 
         tabs = ?
     WHERE id = ?`,
    [requestedExtendMonths, extendTimeInMinutes, tabs, id],
    function (err) {
      if (err) return res.status(500).json({ message: "DB error" });
      res.json({ message: "Yêu cầu gia hạn đã gửi, chờ admin xác nhận" });
    }
  );
});

// 🟢 Admin xác nhận gia hạn
app.patch("/rentals/:id/confirm-extend", authMiddleware, (req, res) => {
  const { id } = req.params;

  db.get(`SELECT * FROM rentals WHERE id = ?`, [id], (err, rental) => {
    if (err || !rental) return res.status(404).json({ message: "Rental không tồn tại" });

    const newRentalTime = rental.rentalTime + (rental.extendTimeInMinutes || 0);

    db.run(
      `UPDATE rentals 
       SET status = 'active', 
           rentalTime = ?, 
           requestedExtendMonths = NULL, 
           extendTimeInMinutes = NULL 
       WHERE id = ?`,
      [newRentalTime, id],
      function (err2) {
        if (err2) return res.status(500).json({ message: "DB error" });
        res.json({ message: "Gia hạn thành công", rentalId: id, newRentalTime });
      }
    );
  });
});

// 🟢 Admin từ chối gia hạn
app.patch("/rentals/:id/reject-extend", authMiddleware, (req, res) => {
  const { id } = req.params;

  db.run(
    `UPDATE rentals 
     SET status = 'active', 
         requestedExtendMonths = NULL, 
         extendTimeInMinutes = NULL 
     WHERE id = ?`,
    [id],
    function (err) {
      if (err) return res.status(500).json({ message: "DB error" });
      res.json({ message: "Đã từ chối gia hạn", rentalId: id });
    }
  );
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
},24 * 60 * 60 * 1000);

// ====== Start ======
app.listen(PORT, () => {
  console.log(`✅ Backend running at http://localhost:${PORT}`);
});
