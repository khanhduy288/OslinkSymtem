const sqlite3 = require("sqlite3").verbose();
const db = new sqlite3.Database("./database.db");

db.serialize(() => {
  db.run("ALTER TABLE rentals ADD COLUMN requestedExtendMonths INTEGER", (err) => {
    if (err) console.log("Cột requestedExtendMonths đã tồn tại hoặc lỗi:", err.message);
  });

  db.run("ALTER TABLE rentals ADD COLUMN extendTimeInMinutes INTEGER", (err) => {
    if (err) console.log("Cột extendTimeInMinutes đã tồn tại hoặc lỗi:", err.message);
  });
});

db.close();
