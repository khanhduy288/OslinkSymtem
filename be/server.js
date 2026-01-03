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
require('dotenv').config();
const SECRET_KEY = process.env.SECRET_KEY;
const ADMIN_TOKEN = process.env.ADMIN_TOKEN;


app.use(cors({
  origin: "*",  // hoặc chỉ định frontend domain
  methods: ["GET","POST","PATCH","DELETE","OPTIONS"],
  allowedHeaders: ["Content-Type","Authorization"],
  credentials: true
}));

app.options("*", (req, res) => {
  res.sendStatus(200);
});

app.use(express.json());
// ====== SQLite init ======
const dbPath = path.resolve(__dirname, "database.db");
const db = new sqlite3.Database(dbPath, (err) => {
  if (err) console.error("❌ SQLite connect error:", err.message);
  else console.log("✅ SQLite connected:", dbPath);
});

// ====== SQLite Promise Helpers ======
const dbRun = (sql, params = []) =>
  new Promise((resolve, reject) => {
    db.run(sql, params, function (err) {
      if (err) reject(err);
      else resolve(this); // this.lastID, this.changes
    });
  });

const dbGet = (sql, params = []) =>
  new Promise((resolve, reject) => {
    db.get(sql, params, (err, row) => {
      if (err) reject(err);
      else resolve(row);
    });
  });

const dbAll = (sql, params = []) =>
  new Promise((resolve, reject) => {
    db.all(sql, params, (err, rows) => {
      if (err) reject(err);
      else resolve(rows);
    });
  });



// ====== 
// Create / migrate users table if not exists ======
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

  // ================== TẠO BẢNG REVENUES ==================
  db.run(`
    CREATE TABLE IF NOT EXISTS revenues (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      rentalId INTEGER,
      amount REAL,
      type TEXT,              -- 'new_rental' hoặc 'extend'
      createdAt TEXT DEFAULT (datetime('now', 'localtime'))
    )
  `);
  
    // Logs table
  db.run(`
    CREATE TABLE IF NOT EXISTS logs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      userId INTEGER,
      username TEXT,
      action TEXT NOT NULL,
      details TEXT,
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

    // ✅ Add new column pricePerTab (nếu chưa tồn tại)
  db.all("PRAGMA table_info(rentals)", (err, columns) => {
    if (!columns.some(c => c.name === "pricePerTab")) {
      db.run(
        "ALTER TABLE rentals ADD COLUMN pricePerTab INTEGER DEFAULT 150000",
        () => console.log("✅ Added column pricePerTab to rentals with default 150000 ✅")
      );
    }
  });
// ✅ Add new column extendVoucherCode (nếu chưa tồn tại)
db.all("PRAGMA table_info(rentals)", (err, columns) => {
  if (!columns.some(c => c.name === "extendVoucherCode")) {
    db.run(
      "ALTER TABLE rentals ADD COLUMN extendVoucherCode TEXT",
      () => console.log("✅ Added column extendVoucherCode to rentals ✅")
    );
  }
});


    // Settings table
  db.run(`
    CREATE TABLE IF NOT EXISTS settings (
      key TEXT PRIMARY KEY,
      value TEXT
    )
  `);

  // ================== VOUCHERS ==================
  db.run(`
    CREATE TABLE IF NOT EXISTS vouchers (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      code TEXT NOT NULL UNIQUE,
      discount_percent INTEGER NOT NULL CHECK(discount_percent > 0 AND discount_percent <= 100),
      expires_at DATETIME NOT NULL,
      max_uses INTEGER,              -- NULL = không giới hạn
      used_count INTEGER DEFAULT 0,
      is_active INTEGER DEFAULT 1,   -- 1 = active, 0 = disabled
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
  `);
  
  // ================== VOUCHER USAGES ==================
db.run(`
  CREATE TABLE IF NOT EXISTS voucher_usages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    voucher_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    used_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(voucher_id, user_id)
  )
`);

db.run(`
  CREATE INDEX IF NOT EXISTS idx_voucher_code
  ON vouchers(code)
`);

  // Set default WORKER_API nếu chưa có
  db.get("SELECT value FROM settings WHERE key = 'WORKER_API'", (err, row) => {
    if (err) console.error(err);
    if (!row) {
      db.run("INSERT INTO settings (key, value) VALUES (?, ?)", [
        "WORKER_API",
        "http://127.0.0.1:5001",
      ]);
      console.log("[INFO] WORKER_API set default = http://127.0.0.1:5001");
    }
  });
});

let WORKER_API = "http://127.0.0.1:5001"; // fallback

db.get("SELECT value FROM settings WHERE key = 'WORKER_API'", (err, row) => {
  if (row && row.value) {
    WORKER_API = row.value;
    console.log("[INFO] Loaded WORKER_API =", WORKER_API);
  }
});

// ====== Helpers ======
function addMinutes(date, minutes) {
  return new Date(date.getTime() + minutes * 60000);
}

function addLog(userId, action, details = "", extra = {}) {
  const extraStr = Object.keys(extra).length
    ? " | " + JSON.stringify(extra)
    : "";

  if (!userId) {
    db.run(
      `INSERT INTO logs (userId, username, action, details, createdAt)
       VALUES (NULL, 'admin', ?, ?, datetime('now', 'localtime'))`,
      [action, details + extraStr],
      (err) => {
        if (err) console.error("[LOG ERROR]", err.message);
      }
    );
    return;
  }

  db.get(`SELECT username FROM users WHERE id = ?`, [userId], (err, row) => {
    const username = row?.username || "unknown";
    db.run(
      `INSERT INTO logs (userId, username, action, details, createdAt)
       VALUES (?, ?, ?, ?, datetime('now', 'localtime'))`,
      [userId, username, action, details + extraStr],
      (err2) => {
        if (err2) console.error("[LOG ERROR]", err2.message);
      }
    );
  });
}


async function addRevenue(rentalId, amount, type = "new_rental") {
  return new Promise((resolve, reject) => {
    db.run(
      `INSERT INTO revenues (rentalId, amount, type) VALUES (?, ?, ?)`,
      [rentalId, amount, type],
      function (err) {
        if (err) reject(err);
        else {
          console.log(`[REVENUE] +${amount} (${type}) -> rentalId=${rentalId}`);
          resolve(this.lastID);
        }
      }
    );
  });
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

function adminAuth(req, res, next) {
  const authHeader = req.headers.authorization || "";
  const token = authHeader.split(" ")[1];

  if (!token) return res.status(401).json({ message: "Thiếu token" });

  // 1️⃣ Trường hợp token admin cứng
  if (token === ADMIN_TOKEN) {
    req.user = { id: 1, level: 100, username: "admin", isAdmin: true };
    return next();
  }

  try {
    // ✅ kiểm tra chữ ký với SECRET_KEY
    const decoded = jwt.verify(token, SECRET_KEY);

    // chỉ cho phép level >=10 vào admin
    if (decoded.level >= 10) {
      req.user = decoded;
      return next();
    }

    return res.status(403).json({ message: "Không đủ quyền" });
  } catch (err) {
    return res.status(401).json({ message: "Token không hợp lệ" });
  }
}


function authMiddleware(req, res, next) {
  const authHeader = req.headers.authorization;
  if (!authHeader) return res.status(401).json({ message: "No token" });

  const token = authHeader.split(" ")[1];
  try {
    // ✅ verify chữ ký số
    const decoded = jwt.verify(token, SECRET_KEY);
    req.user = decoded; // { id, level, username? }
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


app.use("/admin", adminAuth);

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

app.post("/login", async (req, res) => {
  const { phone, password } = req.body;
  if (!phone || !password) return res.status(400).json({ message: "Missing params" });

  db.get(`SELECT * FROM users WHERE phone=?`, [phone], async (err, user) => {
    if (err) return res.status(500).json({ message: "DB error" });
    if (!user) return res.status(404).json({ message: "Số điện thoại không tồn tại" });

    const match = await bcrypt.compare(password, user.password);
    if (!match) return res.status(400).json({ message: "Mật khẩu không đúng" });

    const token = jwt.sign(
      { id: user.id, level: user.level },  // payload
      SECRET_KEY,
      { expiresIn: "365d" }              // chữ ký số đảm bảo không fake được
    );

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

app.post("/rentals", authMiddleware, async (req, res) => {
  try {
    // Gán voucherCode mặc định null nếu FE không gửi
    const { username, tabs, months, pricePerTab, voucherCode = null } = req.body;

    if (!username || !tabs || !months || !pricePerTab)
      return res.status(400).json({ message: "Thiếu dữ liệu" });

    const userId = req.user.id;

    // ================== TÍNH GIÁ GỐC ==================
    let totalPrice = tabs * months * pricePerTab;
    let voucher = null;

    // ================== ÁP VOUCHER (NẾU CÓ) ==================
    if (voucherCode) {
      voucher = await dbGet(
        `SELECT * FROM vouchers WHERE code = ? AND is_active = 1`,
        [voucherCode.toUpperCase()]
      );

      if (!voucher)
        return res.status(400).json({ message: "Voucher không hợp lệ" });

      if (new Date(voucher.expires_at) < new Date())
        return res.status(400).json({ message: "Voucher đã hết hạn" });

      if (voucher.max_uses && voucher.used_count >= voucher.max_uses)
        return res.status(400).json({ message: "Voucher đã hết lượt dùng" });

      const used = await dbGet(
        `SELECT 1 FROM voucher_usages WHERE voucher_id = ? AND user_id = ?`,
        [voucher.id, userId]
      );

      if (used)
        return res.status(400).json({ message: "Voucher đã được sử dụng" });

      totalPrice = Math.round(totalPrice * (100 - 0) / 100);
    }

    // ================== TÍNH THỜI GIAN ==================
    const rentalTime = months * 30 * 24 * 60; // phút
    const expiresAt = new Date(Date.now() + rentalTime * 60 * 1000).toISOString();

    // ================== TẠO RENTAL ==================
    const result = await dbRun(
      `INSERT INTO rentals
       (userId, rentalTime, expiresAt, tabs, pricePerTab, status)
       VALUES (?, ?, ?, ?, ?, 'pending')`,
      [userId, rentalTime, expiresAt, tabs, pricePerTab]
    );

    const rentalId = result.lastID;

    // ================== GHI VOUCHER (SAU KHI THÀNH CÔNG) ==================
    if (voucher) {
      await dbRun(
        `INSERT INTO voucher_usages (voucher_id, user_id) VALUES (?, ?)`,
        [voucher.id, userId]
      );
      await dbRun(
        `UPDATE vouchers SET used_count = used_count + 1 WHERE id = ?`,
        [voucher.id]
      );
    }

    addLog(userId, "Thuê tab", `tabs=${tabs}, months=${months}, price=${totalPrice}`);

    res.json({ message: "Thuê tab thành công", rentalId, totalPrice });
  } catch (err) {
    console.error(err);
    res.status(500).json({ message: "Lỗi thuê tab", error: err.message });
  }
});



// 🟢 API: Lấy username theo userId
app.get("/admin/getUsernameById/:userId", (req, res) => {
  const { userId } = req.params;

  if (!userId) {
    return res.status(400).json({ message: "Thiếu userId" });
  }

  const query = "SELECT username FROM users WHERE id = ?";

  db.get(query, [userId], (err, row) => {
    if (err) {
      console.error("[DB ERROR]", err.message);
      return res.status(500).json({ message: "Lỗi truy vấn CSDL" });
    }

    if (!row) {
      return res.status(404).json({ message: "Không tìm thấy userId" });
    }

    return res.json({ userId, username: row.username });
  });
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

app.get("/admin/rentals", adminAuth, (req, res) => {
  const { userId, _sort, _order, _limit } = req.query;

  let sql = `
    SELECT rentals.*,
           users.username
    FROM rentals
    LEFT JOIN users ON rentals.userId = users.id
  `;
  const params = [];

  if (userId) {
    sql += " WHERE rentals.userId = ?";
    params.push(userId);
  }

  if (_sort) {
    sql += ` ORDER BY ${_sort} ${
      _order && _order.toUpperCase() === "DESC" ? "DESC" : "ASC"
    }`;
  }

  if (_limit) {
    sql += ` LIMIT ${parseInt(_limit)}`;
  }

  db.all(sql, params, (err, rows) => {
    if (err) return res.status(500).json({ message: err.message });
    res.json(rows);
  });
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
  const { status, roomCode, rentalTime, action, expiresAt } = req.body;

  db.get(`SELECT * FROM rentals WHERE id=?`, [id], (err, rental) => {
    if (err) return res.status(500).json({ message: "DB error" });
    if (!rental) return res.status(404).json({ message: "Rental not found" });

    const newStatus = status ?? rental.status;
    const newRentalTime = rentalTime ?? rental.rentalTime;
    const newRoomCode = roomCode ?? rental.roomCode;

    // ================== CASE 1: XÁC NHẬN ĐỔI TAB ==================
    if (
      rental.status === "pending_change_tab" &&
      newStatus === "active" &&
      action === "confirm_change_tab"
    ) {
      console.log(`[INFO] Xác nhận đổi tab cho rentalId=${id}, roomCode=${rental.roomCode}`);
      axios
        .post(`${WORKER_API}/command`, {
          action: "change_devide",
          userId: rental.userId,
          rentalId: id,
          roomCode: rental.roomCode,
        })
        .then(() => {
          db.run(
            `UPDATE rentals SET status='active', rentalTime=? WHERE id=?`,
            [newRentalTime, id],
            () => {
              addLog(
                req.user?.id,
                "Xác nhận đổi tab",
                `Rental #${id} (${rental.roomCode}) đã được xác nhận đổi tab`
              );

              // 🟢 Ghi doanh thu nếu trước đó là pending/retrieved
              if (["pending", "retrieved"].includes(rental.status)) {
                const months = rental.rentalTime / 43200;
                const amount =
                  (rental.pricePerTab || 150000) * (rental.tabs || 1) * months;

                db.run(
                  `INSERT INTO revenues (rentalId, amount, type, createdAt)
                   VALUES (?, ?, 'new_rental', datetime('now','localtime'))`,
                  [id, amount],
                  (errRev) => {
                    if (errRev)
                      console.error("❌ Lỗi ghi doanh thu:", errRev.message);
                    else
                      console.log(
                        `✅ Ghi doanh thu rental #${id} (${rental.roomCode}): ${amount}`
                      );
                  }
                );
              }

              res.json({
                message: "Đã gửi change_devide cho worker",
                rentalId: id,
              });
            }
          );
        })
        .catch((e) =>
          res.status(500).json({ message: "Worker API error", error: e.message })
        );
      return;
    }

    // ================== CASE 2: HỦY YÊU CẦU ĐỔI TAB ==================
    if (
      rental.status === "pending_change_tab" &&
      newStatus === "active" &&
      action === "cancel_change_tab"
    ) {
      console.log(`[INFO] Hủy yêu cầu đổi tab rentalId=${id}`);
      db.run(
        `UPDATE rentals SET status='active', rentalTime=? WHERE id=?`,
        [newRentalTime, id],
        () => {
          addLog(
            req.user?.id,
            "Hủy yêu cầu đổi tab",
            `Rental #${id} (${rental.roomCode}) quay lại trạng thái active`
          );

          if (["pending", "retrieved"].includes(rental.status)) {
            const months = rental.rentalTime / 43200;
            const amount =
              (rental.pricePerTab || 150000) * (rental.tabs || 1) * months;

            db.run(
              `INSERT INTO revenues (rentalId, amount, type, createdAt)
               VALUES (?, ?, 'new_rental', datetime('now','localtime'))`,
              [id, amount],
              (errRev) => {
                if (errRev)
                  console.error("❌ Lỗi ghi doanh thu:", errRev.message);
                else
                  console.log(
                    `✅ Ghi doanh thu rental #${id} (${rental.roomCode}): ${amount}`
                  );
              }
            );
          }

          res.json({
            message: "Đã hủy yêu cầu đổi tab, quay lại active",
            rentalId: id,
          });
        }
      );
      return;
    }

    // ================== CASE 3: KÍCH HOẠT RENTAL MỚI ==================
    if (newStatus === "active" && !newRoomCode) {
      db.get(
        `SELECT * FROM rentals WHERE userId=? AND status='active' AND id!=?`,
        [rental.userId, id],
        (err2, activeRental) => {
          if (err2) return res.status(500).json({ message: "DB error" });

          if (activeRental) {
            // extend_room
            console.log(`[INFO] User ${rental.userId} đã có rental active, gửi extend_room`);
            axios
              .post(`${WORKER_API}/command`, {
                action: "extend_room",
                userId: rental.userId,
                rentalId: id,
                rentalTime: newRentalTime,
                roomCode: activeRental.roomCode,
              })
              .then(() => {
                db.run(
                  `UPDATE rentals SET status='active', rentalTime=? WHERE id=?`,
                  [newRentalTime, id],
                  () => {
                    addLog(
                      req.user?.id,
                      "Gia hạn rental",
                      `Rental #${id} của userId=${rental.userId} được extend thêm ${newRentalTime} phút`
                    );

                    if (["pending", "retrieved"].includes(rental.status)) {
                      const months = newRentalTime / 43200;
                      const amount =
                        (rental.pricePerTab || 150000) * (rental.tabs || 1) * months;

                      db.run(
                        `INSERT INTO revenues (rentalId, amount, type, createdAt)
                         VALUES (?, ?, 'extend', datetime('now','localtime'))`,
                        [id, amount],
                        (errRev) => {
                          if (errRev)
                            console.error("❌ Lỗi ghi doanh thu (extend):", errRev.message);
                          else
                            console.log(
                              `✅ Ghi doanh thu gia hạn rental #${id}: ${amount}`
                            );
                        }
                      );
                    }

                    res.json({
                      message: "Đã gửi extend_room cho worker",
                      rentalId: id,
                    });
                  }
                );
              })
              .catch((e) =>
                res.status(500).json({ message: "Worker API error", error: e.message })
              );
          } else {
            // create_room
            axios
              .post(`${WORKER_API}/command`, {
                action: "create_room",
                userId: rental.userId,
                rentalId: id,
                rentalTime: newRentalTime,
              })
              .then(() => {
                db.run(
                  `UPDATE rentals SET status='active', rentalTime=? WHERE id=?`,
                  [newRentalTime, id],
                  () => {
                    addLog(
                      req.user?.id,
                      "Kích hoạt rental",
                      `Rental #${id} được chuyển sang trạng thái active`
                    );

                    if (["pending", "retrieved"].includes(rental.status)) {
                      const months = newRentalTime / 43200;
                      const amount =
                        (rental.pricePerTab || 150000) * (rental.tabs || 1) * months;

                      db.run(
                        `INSERT INTO revenues (rentalId, amount, type, createdAt)
                         VALUES (?, ?, 'new_rental', datetime('now','localtime'))`,
                        [id, amount],
                        (errRev) => {
                          if (errRev)
                            console.error("❌ Lỗi ghi doanh thu:", errRev.message);
                          else
                            console.log(
                              `✅ Ghi doanh thu rental #${id} (${rental.roomCode}): ${amount}`
                            );
                        }
                      );
                    }

                    res.json({
                      message: "Đã gửi create_room cho worker",
                      rentalId: id,
                    });
                  }
                );
              })
              .catch((e) =>
                res.status(500).json({ message: "Worker API error", error: e.message })
              );
          }
        }
      );
      return;
    }

    // ================== CASE 4: ADMIN CHỈNH HẠN / TRỰC TIẾP ==================
    const fields = [];
    const values = [];

    if (status !== undefined) { fields.push("status=?"); values.push(status); }
    if (rentalTime !== undefined) { fields.push("rentalTime=?"); values.push(rentalTime); }
    if (roomCode !== undefined) { fields.push("roomCode=?"); values.push(roomCode); }
    if (expiresAt !== undefined) {
      const parsed = new Date(expiresAt);
      if (isNaN(parsed.getTime()))
        return res.status(400).json({ message: "expiresAt không hợp lệ" });
      fields.push("expiresAt=?");
      values.push(parsed.toISOString());
    }

    if (fields.length === 0)
      return res.json({ message: "Không có gì để cập nhật" });

    db.run(`UPDATE rentals SET ${fields.join(", ")} WHERE id=?`, [...values, id], function (err3) {
      if (err3) return res.status(500).json({ message: err3.message });

      const changes = fields.map((f, idx) => `${f.split("=")[0]}=${values[idx]}`);
      addLog(
        req.user?.id,
        "Chỉnh sửa rental",
        `Rental #${id} chỉnh sửa các trường: ${changes.join(", ")}`
      );

      // 🟢 Ghi doanh thu nếu trước đó là pending/retrieved
      if (["pending", "retrieved"].includes(rental.status) && status === "active") {
        const months = rental.rentalTime / 43200;
        const amount =
          (rental.pricePerTab || 150000) * (rental.tabs || 1) * months;

        db.run(
          `INSERT INTO revenues (rentalId, amount, type, createdAt)
           VALUES (?, ?, 'new_rental', datetime('now','localtime'))`,
          [id, amount],
          (errRev) => {
            if (errRev) console.error("❌ Lỗi ghi doanh thu:", errRev.message);
            else
              console.log(
                `✅ Ghi doanh thu rental #${id} (${rental.roomCode}): ${amount}`
              );
          }
        );
      }

      db.get(`SELECT * FROM rentals WHERE id=?`, [id], (err4, updated) => {
        if (err4) return res.status(500).json({ message: "DB error" });
        res.json({ message: "Cập nhật thành công", rental: updated });
      });
    });
  });
});


app.delete("/rentals/:id", (req, res) => {
  const { id } = req.params;

  // Lấy rental trước khi xóa
  db.get("SELECT * FROM rentals WHERE id = ?", [id], (err, rental) => {
    if (err) return res.status(500).json({ message: "DB error" });
    if (!rental) return res.status(404).json({ message: "Rental not found" });

    // Ghi log trước khi xóa
    addLog(
      req.user?.id,
      "Xóa đơn",
      `Rental #${id} đã bị xóa khỏi hệ thống`,
      { roomCode: rental.roomCode, tabs: rental.tabs }
    );

    // Xóa rental
    db.run("DELETE FROM rentals WHERE id = ?", [id], function (err2) {
      if (err2) return res.status(500).json({ message: "DB error khi xóa" });
      res.json({ message: "Rental deleted successfully" });
    });
  });
});


app.delete("/admin/users/:id", authMiddleware, (req, res) => {
  // Chỉ admin level >= 10 mới được xóa
  if (req.user.level < 10) return res.status(403).json({ message: "Không đủ quyền" });

  const { id } = req.params;
  db.run("DELETE FROM users WHERE id = ?", [id], function(err) {
    if (err) return res.status(500).json({ message: err.message });
    if (this.changes === 0) return res.status(404).json({ message: "User not found" });
    addLog(
      req.user?.id,
      "Xóa user",
      `Xóa user ID=${id}`
    );
    res.json({ message: `User ${id} deleted successfully` });
  });
});

// ====== Admin reset password ======
app.post("/admin/reset-password", authMiddleware, async (req, res) => {
  // Check admin
  if (req.user.level < 10) return res.status(403).json({ message: "Không đủ quyền" });

  const { userId, newPassword } = req.body;
  if (!userId || !newPassword) return res.status(400).json({ message: "Missing params" });

  // Kiểm tra độ dài mật khẩu
  if (!isValidPassword(newPassword))
    return res.status(400).json({ message: "Mật khẩu phải >= 6 ký tự" });

  try {
    // Hash mật khẩu mới
    const hashed = await bcrypt.hash(newPassword, 10);

    // Cập nhật DB
    db.run(`UPDATE users SET password=? WHERE id=?`, [hashed, userId], function(err){
      if(err) return res.status(500).json({ message: "DB error", error: err.message });
      res.json({ message: `Đã reset mật khẩu cho user ${userId}` });
    });
  } catch(e) {
    console.error(e);
    res.status(500).json({ message: "Server error", error: e.message });
  }
});

app.get("/admin/logs", adminAuth, (req, res) => {
  db.all("SELECT * FROM logs ORDER BY datetime(createdAt) DESC LIMIT 200", [], (err, rows) => {
    if (err) return res.status(500).json({ message: err.message });
    res.json(rows);
  });
});

app.get("/user/logs", authMiddleware, (req, res) => {
  db.all("SELECT * FROM logs WHERE userId=? ORDER BY datetime(createdAt) DESC", [req.user.id], (err, rows) => {
    if (err) return res.status(500).json({ message: err.message });
    res.json(rows);
  });
});


// 🟢 User gửi yêu cầu gia hạn | Admin request hộ user
app.post("/rentals/:id/request-extend", authMiddleware, async (req, res) => {
  try {
    const { id } = req.params;
    const { months, voucherCode } = req.body;

    if (!months || months <= 0) {
      return res.status(400).json({ message: "Thiếu số tháng gia hạn" });
    }

    // 🔥 KHÔNG check userId nữa
    const rental = await dbGet(
      "SELECT * FROM rentals WHERE id = ?",
      [id]
    );

    if (!rental) {
      return res.status(404).json({ message: "Không tìm thấy rental" });
    }

    // 🧾 Validate voucher sớm (nếu có)
    if (voucherCode) {
      const voucher = await dbGet(
        `SELECT id FROM vouchers
         WHERE code = ? AND is_active = 1`,
        [voucherCode.toUpperCase()]
      );

      if (!voucher) {
        return res.status(400).json({ message: "Voucher không hợp lệ" });
      }
    }

    await dbRun(
      `UPDATE rentals
       SET status = 'pending_extend',
           requestedExtendMonths = ?,
           extendVoucherCode = ?
       WHERE id = ?`,
      [
        months,
        voucherCode ? voucherCode.toUpperCase() : null,
        id
      ]
    );

    res.json({
      message: "Đã gửi yêu cầu gia hạn"
    });
  } catch (err) {
    console.error(err);
    res.status(500).json({ message: "Lỗi request extend" });
  }
});


app.patch("/rentals/:id/confirm-extend", authMiddleware, async (req, res) => {
  try {
    const { id } = req.params;

    const rental = await dbGet(
      "SELECT * FROM rentals WHERE id = ?",
      [id]
    );

    if (!rental) {
      return res.status(404).json({ message: "Rental không tồn tại" });
    }

    if (rental.status !== "pending_extend") {
      return res.status(400).json({ message: "Rental chưa có yêu cầu gia hạn" });
    }

    const now = Date.now();
    const currentExpires = rental.expiresAt
      ? new Date(rental.expiresAt).getTime()
      : 0;

    const extendMonths = rental.requestedExtendMonths || 1;
    const extendMinutes = extendMonths * 30 * 24 * 60;

    const baseTime = currentExpires > now ? currentExpires : now;
    const newExpiresAt = new Date(
      baseTime + extendMinutes * 60000
    ).toISOString();

    // ================== GIÁ ==================
    const pricePerTab = rental.pricePerTab || 150000;
    const tabCount = rental.tabs || 1;

    let finalPrice = extendMonths * pricePerTab * tabCount;
    let discountPercent = 0;

    // ================== VOUCHER ==================
    if (rental.extendVoucherCode) {
      const voucher = await dbGet(
        `SELECT * FROM vouchers
         WHERE code = ? AND is_active = 1`,
        [rental.extendVoucherCode]
      );

      if (
        voucher &&
        new Date(voucher.expires_at) > new Date() &&
        (!voucher.max_uses || voucher.used_count < voucher.max_uses)
      ) {
        const used = await dbGet(
          `SELECT 1 FROM voucher_usages
           WHERE voucher_id = ? AND user_id = ?`,
          [voucher.id, rental.userId]
        );

        if (!used) {
          discountPercent = voucher.discount_percent || 0;
          finalPrice = Math.round(finalPrice * (100 - discountPercent) / 100);

          await dbRun(
            "INSERT INTO voucher_usages (voucher_id, user_id) VALUES (?, ?)",
            [voucher.id, rental.userId]
          );

          await dbRun(
            "UPDATE vouchers SET used_count = used_count + 1 WHERE id = ?",
            [voucher.id]
          );
        }
      }
    }

    // ================== UPDATE RENTAL ==================
    await dbRun(
      `UPDATE rentals
       SET status = 'active',
           expiresAt = ?,
           requestedExtendMonths = NULL,
           extendVoucherCode = NULL
       WHERE id = ?`,
      [newExpiresAt, id]
    );

    res.json({
      message: "Gia hạn thành công",
      rentalId: id,
      newExpiresAt,
      finalPrice,
      discountPercent
    });
  } catch (err) {
    console.error(err);
    res.status(500).json({ message: "Lỗi confirm extend" });
  }
});

app.patch("/rentals/:id/reject-extend", authMiddleware, async (req, res) => {
  if (req.user.role !== "admin") {
    return res.status(403).json({ message: "Chỉ admin mới được từ chối" });
  }

  const { id } = req.params;

  try {
    await dbRun(
      `UPDATE rentals
       SET status = 'active',
           requestedExtendMonths = NULL,
           extendTimeInMinutes = NULL,
           extendVoucherCode = NULL
       WHERE id = ?`,
      [id]
    );

    res.json({
      message: "Đã từ chối gia hạn",
      rentalId: id
    });
  } catch (err) {
    res.status(500).json({ message: "DB error" });
  }
});

// ====== API cho admin lấy WORKER_API hiện tại ======
app.get("/admin/worker", authMiddleware, (req, res) => {
  if (req.user.level < 10) return res.status(403).json({ message: "Không đủ quyền" });

  db.get("SELECT value FROM settings WHERE key = 'WORKER_API'", (err, row) => {
    if (err) {
      console.error("[DB ERROR]", err);
      return res.status(500).json({ message: "DB error" });
    }
    res.json({ WORKER_API: row ? row.value : WORKER_API });
  });
});

// ====== API cho admin update WORKER_API ======
app.patch("/admin/worker", authMiddleware, (req, res) => {
  if (req.user.level < 10) return res.status(403).json({ message: "Không đủ quyền" });

  const { url } = req.body;
  if (!url || !url.startsWith("http")) {
    return res.status(400).json({ message: "URL không hợp lệ" });
  }

  WORKER_API = url;
  db.run("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ["WORKER_API", url], (err) => {
    if (err) {
      console.error("[DB ERROR]", err);
      return res.status(500).json({ message: "DB error" });
    }
    console.log("[INFO] WORKER_API updated =", WORKER_API);
    res.json({ message: "WORKER_API updated", WORKER_API });
  });
});

app.get("/api/room-groups", authMiddleware, (req, res) => {
  const query = `
    SELECT r.roomCode, r.status, r.expiresAt, r.createdAt, u.phone, u.username
    FROM rentals r
    LEFT JOIN users u ON r.userId = u.id
  `;

  db.all(query, [], (err, rows) => {
    if (err) {
      console.error("DB error:", err);
      return res.status(500).json({ error: "Database error" });
    }

    if (!rows || rows.length === 0) return res.json([]);

    const groups = {};

    for (const row of rows) {
      const roomCodeStr = (row.roomCode ?? "").toString().trim();
      if (!roomCodeStr) continue;

      const parts = roomCodeStr.split(" ");
      if (parts.length >= 3) {
        const groupKey = parts[2];

        if (!groups[groupKey]) {
          groups[groupKey] = {
            group: groupKey,
            rooms: [],
            phones: [],
            usernames: [],
            statuses: [],
            remainingTimes: []
          };
        }

        // ✅ Push room object with createdAt
        groups[groupKey].rooms.push({
          roomCode: roomCodeStr,
          createdAt: row.createdAt || null
        });

        groups[groupKey].phones.push(row.phone || null);
        groups[groupKey].usernames.push(row.username || null);
        groups[groupKey].statuses.push(row.status);

        // ✅ Tính remaining time
        let remaining = null;
        if (row.expiresAt) {
          const expireTime = new Date(row.expiresAt).getTime();
          const nowTime = Date.now();
          remaining = Math.max(0, Math.floor((expireTime - nowTime) / 60000));
        }
        groups[groupKey].remainingTimes.push(remaining);
      }
    }

    const result = Object.values(groups).map(g => ({
      group: g.group,
      count: g.rooms.length,
      rooms: g.rooms, // ✅ Object format
      phones: g.phones,
      usernames: g.usernames,
      expired: g.statuses.every(s => s === "expired"),
      remainingTimes: g.remainingTimes,
    }));

    res.json(result);
  });
});

// Lịch sử doanh thu
app.get("/admin/revenue/history", authMiddleware, (req, res) => {
  db.all("SELECT * FROM revenues ORDER BY createdAt DESC", (err, rows) => {
    if (err) return res.status(500).json({ message: "DB error" });
    res.json(rows);
  });
});


app.get("/admin/lastRoomCode/:userId", (req, res) => {
  const { userId } = req.params;

  db.get(
    `SELECT roomCode
     FROM rentals
     WHERE userId=? AND roomCode IS NOT NULL
     ORDER BY createdAt DESC
     LIMIT 1`,
    [userId],
    (err, row) => {
      if (err) {
        console.error("DB error:", err);
        return res.status(500).json({ message: "DB error" });
      }
      if (!row) {
        return res.status(404).json({ message: "Không tìm thấy roomCode" });
      }
      res.json({ userId, roomCode: row.roomCode });
    }
  );
});

app.post("/import-revenues", (req, res) => {
  db.all("SELECT * FROM rentals WHERE status = 'active'", async (err, rentals) => {
    if (err) {
      console.error(err);
      return res.status(500).json({ message: "Lỗi khi đọc rentals" });
    }

    let imported = 0;

    const insertPromises = rentals.map((rental) => {
      return new Promise((resolve) => {
        const months = rental.rentalTime / 43200;
        const amount = months * rental.pricePerTab * (rental.tabs || 1);

        db.get("SELECT * FROM revenues WHERE rentalId = ?", [rental.id], (err2, existing) => {
          if (err2) {
            console.error(err2);
            return resolve(false);
          }

          if (!existing) {
            db.run(
              "INSERT INTO revenues (rentalId, userId, amount, createdAt) VALUES (?, ?, ?, ?)",
              [rental.id, rental.userId, amount, rental.createdAt],
              (err3) => {
                if (err3) console.error(err3);
                else imported++;
                resolve(true);
              }
            );
          } else {
            resolve(false);
          }
        });
      });
    });

    await Promise.all(insertPromises);
    res.json({ message: `✅ Đã import doanh thu cho ${rentals.length} đơn active (đã thêm mới ${imported} đơn).` });
  });
});


app.delete("/revenues/:id", (req, res) => {
  const id = req.params.id;
  db.run("DELETE FROM revenues WHERE id = ?", [id], err => {
    if (err) {
      console.error(err);
      return res.status(500).json({ message: "Lỗi khi xóa bản ghi." });
    }
    res.json({ message: `🗑️ Đã xóa bản ghi revenues có id = ${id}` });
  });
});

app.patch("/rentals/:id/compensate", adminAuth, async (req, res) => {
  try {
    const { compensateMinutes } = req.body;
    const { id } = req.params;

    if (!compensateMinutes || compensateMinutes <= 0) {
      return res.status(400).json({ message: "compensateMinutes > 0" });
    }

    const rental = await new Promise((resolve, reject) => {
      db.get(`SELECT * FROM rentals WHERE id = ?`, [id], (err, row) => {
        if (err) reject(err);
        else resolve(row);
      });
    });

    if (!rental) {
      return res.status(404).json({ message: "Rental không tồn tại" });
    }

    // ✅ CHỈ bù cho đơn active & còn hạn
    if (
      rental.status !== "active" ||
      !rental.expiresAt ||
      new Date(rental.expiresAt).getTime() <= Date.now()
    ) {
      return res.status(400).json({ message: "Đơn không còn hạn" });
    }

    const newExpiresAt = new Date(
      new Date(rental.expiresAt).getTime() + compensateMinutes * 60000
    ).toISOString();

    await new Promise((resolve, reject) => {
      db.run(
        `UPDATE rentals SET expiresAt = ? WHERE id = ?`,
        [newExpiresAt, id],
        (err) => (err ? reject(err) : resolve())
      );
    });

    // ✅ log lại hành động
    addLog(
      req.user?.id,
      "Bù thời gian",
      `Bù ${compensateMinutes} phút cho rental #${id}`
    );

    res.json({
      success: true,
      rentalId: id,
      newExpiresAt,
    });
  } catch (err) {
    console.error("❌ COMPENSATE ERROR:", err);
    res.status(500).json({ message: "Server error", error: err.message });
  }
});

app.post("/admin/vouchers", adminAuth, async (req, res) => {
  try {
    const { code, discountPercent, expiresAt, maxUses } = req.body;
    if (!code || !discountPercent)
      return res.status(400).json({ message: "Thiếu dữ liệu" });

    await dbRun(
      `INSERT INTO vouchers (code, discount_percent, expires_at, max_uses)
       VALUES (?, ?, ?, ?)`,
      [
        code.toUpperCase(),
        discountPercent,
        expiresAt || null,
        maxUses || null
      ]
    );

    res.json({ success: true });
  } catch (err) {
    if (err.message.includes("UNIQUE"))
      return res.status(400).json({ message: "Voucher đã tồn tại" });
    console.error(err);
    res.status(500).json({ message: "Lỗi tạo voucher" });
  }
});

app.get("/admin/vouchers", adminAuth, async (req, res) => {
  try {
    const rows = await dbAll(
      "SELECT * FROM vouchers ORDER BY created_at DESC"
    );
    res.json(rows);
  } catch (err) {
    console.error(err);
    res.status(500).json({ message: "Lỗi lấy danh sách voucher" });
  }
});

app.patch("/admin/vouchers/:id", adminAuth, async (req, res) => {
  try {
    const { isActive, expiresAt, discountPercent, maxUses } = req.body;

    await dbRun(
      `UPDATE vouchers
       SET is_active = ?,
           expires_at = ?,
           discount_percent = ?,
           max_uses = ?
       WHERE id = ?`,
      [
        isActive,
        expiresAt || null,
        discountPercent,
        maxUses || null,
        req.params.id
      ]
    );

    res.json({ success: true });
  } catch (err) {
    console.error(err);
    res.status(500).json({ message: "Lỗi cập nhật voucher" });
  }
});


app.delete("/admin/vouchers/:id", adminAuth, async (req, res) => {
  try {
    await dbRun("DELETE FROM vouchers WHERE id = ?", [req.params.id]);
    res.json({ success: true });
  } catch (err) {
    console.error(err);
    res.status(500).json({ message: "Lỗi xóa voucher" });
  }
});

app.post("/vouchers/validate", authMiddleware, async (req, res) => {
  try {
    const { code } = req.body;
    const userId = req.user.id;

    const voucher = await dbGet(
      `SELECT * FROM vouchers
       WHERE code = ? AND is_active = 1`,
      [code.toUpperCase()]
    );

    if (!voucher)
      return res.status(400).json({ message: "Voucher không tồn tại" });

    if (voucher.expires_at && new Date(voucher.expires_at) < new Date())
      return res.status(400).json({ message: "Voucher đã hết hạn" });

    if (voucher.max_uses && voucher.used_count >= voucher.max_uses)
      return res.status(400).json({ message: "Voucher đã hết lượt dùng" });

    const used = await dbGet(
      `SELECT 1 FROM voucher_usages
       WHERE voucher_id = ? AND user_id = ?`,
      [voucher.id, userId]
    );

    if (used)
      return res.status(400).json({ message: "Bạn đã dùng voucher này" });

    res.json({
      discountPercent: voucher.discount_percent
    });
  } catch (err) {
    console.error(err);
    res.status(500).json({ message: "Lỗi kiểm tra voucher" });
  }
});




// ====== Background auto-expire (mỗi 30 giây) ======
setInterval(() => {
  const nowSql = toSqlDateTime(new Date());

  // Truy vấn các rental đã hết hạn
  db.all(
    `SELECT id, roomCode
     FROM rentals
     WHERE status = 'active'
       AND expiresAt IS NOT NULL
       AND expiresAt <= ?`,
    [nowSql],
    (err, rows) => {
      if (err) {
        console.error("Lỗi khi kiểm tra hết hạn:", err.message);
        return;
      }

      if (!rows || rows.length === 0) return;

      console.log(`⏰ Phát hiện ${rows.length} rental đã hết hạn.`);

      // Duyệt từng rental và update trạng thái
      const stmt = db.prepare(`UPDATE rentals SET status='expired' WHERE id=?`);
      for (const rental of rows) {
        stmt.run(rental.id, (updateErr) => {
          if (updateErr) {
            console.error("❌ Lỗi update status:", updateErr.message);
          } else {
            console.log(`✅ Rental ID=${rental.id}, roomCode=${rental.roomCode} đã hết hạn.`);
          }
        });
      }
      stmt.finalize();
    }
  );
}, 30 * 1000); // 30 giây/lần


// ====== Start ======
app.listen(PORT, () => {
  console.log(`✅ Backend running at http://localhost:${PORT}`);
});
