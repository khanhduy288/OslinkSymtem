        // server.js
        const express = require("express");
        const cors = require("cors");
        const sqlite3 = require("sqlite3").verbose();
        const path = require("path");
        const axios = require('axios');

        const app = express();
        const PORT = 5000;
        const { spawn } = require("child_process");

        app.use(cors());
        app.use(express.json());

        // ====== SQLite init ======
        const dbPath = path.resolve(__dirname, "database.db");
        const db = new sqlite3.Database(dbPath, (err) => {
        if (err) console.error("❌ SQLite connect error:", err.message);
        else console.log("✅ SQLite connected:", dbPath);
        });

        db.serialize(() => {
        db.run(`
            CREATE TABLE IF NOT EXISTS rentals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            userId TEXT NOT NULL,
            rentalTime INTEGER NOT NULL,       -- phút
            roomCode TEXT,                     -- sẽ có sau khi tool tạo room
            status TEXT NOT NULL DEFAULT 'pending', -- pending | active | expired | failed
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


app.post("/rentals", async (req, res) => {
  try {
    const { userId, rentalTime } = req.body;
    if (!userId || !rentalTime) {
      return res.status(400).json({ message: "userId & rentalTime required" });
    }

    // 1. Tạo record "pending"
    const now = new Date();
    const expiresAt = toSqlDateTime(addMinutes(now, rentalTime));

    const rentalId = await new Promise((resolve, reject) => {
      db.run(
        `INSERT INTO rentals (userId, rentalTime, status, expiresAt) VALUES (?, ?, 'pending', ?)`,
        [userId, rentalTime, expiresAt],
        function (err) {
          if (err) return reject(err);
          resolve(this.lastID);
        }
      );
    });

    // 2. Gọi Python API (gửi rentalId để Python tự PATCH ngược về)
axios.post("http://127.0.0.1:5001/command", {
  action: "create_room",
  userId,
  rentalTime,
  rentalId, // 👈 gửi sang cho Python
}).then(res => {
  console.log("✅ Đã gọi Python, status:", res.status);
}).catch(err => {
  console.error("❌ Lỗi gọi Python:", err.message);
});


    // 3. Trả về ngay record pending cho FE
    db.get(`SELECT * FROM rentals WHERE id=?`, [rentalId], (err, row) => {
      if (err) return res.status(500).json({ message: "DB error" });
      res.json({
        message: "Rental created (pending, sẽ được active sau khi tool tạo room)",
        rental: row,
      });
    });
  } catch (err) {
    console.error("Error:", err);
    res.status(500).json({ error: "Failed to create rental" });
  }
});



        /**
         * Danh sách thuê
         */
        app.get("/rentals", (req, res) => {
        db.all(
            `SELECT * FROM rentals ORDER BY createdAt DESC`,
            [],
            (err, rows) => {
            if (err) return res.status(500).json({ message: "DB error" });
            res.json(rows);
            }
        );
        });

        /**
         * Chi tiết 1 rental
         */
        app.get("/rentals/:id", (req, res) => {
        db.get(`SELECT * FROM rentals WHERE id=?`, [req.params.id], (err, row) => {
            if (err) return res.status(500).json({ message: "DB error" });
            if (!row) return res.status(404).json({ message: "Not found" });
            res.json(row);
        });
        });

        /**
         * Hết hạn / thu hồi: gọi tool đóng room + xoá thiết bị (giả lập)
         */
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
            return res
                .status(400)
                .json({ message: `Không ở trạng thái active (status=${rental.status})` });

            // Gọi tool đóng room
            try {
            await runAutomation("close_room", [String(rental.roomCode || "")]);
            } catch (e) {
            return res
                .status(500)
                .json({ message: "Tool automation lỗi khi đóng room", error: e.message });
            }

            // update DB -> expired
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

        /**
         * Gia hạn (optional): cộng thêm phút & cập nhật expiresAt
         * Body: { minutes: number }
         */
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


        // ====== Background auto-expire (mỗi 60s) ======
        setInterval(() => {
        const nowSql = toSqlDateTime(new Date());
        // lấy rental quá hạn và đang active
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
