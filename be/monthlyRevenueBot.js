const axios = require("axios");
const fs = require("fs");
const XLSX = require("xlsx");
const FormData = require("form-data");

// ⚡ Cấu hình
const BOT_TOKEN = "8300892727:AAEaG0EYfXD_wbiaAbeJzLfIVyJNUO7mfuE"; // bot token
const CHAT_ID = "-1003383407105"; // chat id group
const API_BASE = "https://api.tabtreo.com";
const TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6MSwibGV2ZWwiOjEwMCwiaWF0IjoxNzU4ODkwODAzLCJleHAiOjE3OTA0MjY4MDN9.MUA_jjdewR-jIvRdDaFoOz6ZMJHqcG5PRW5WCNv7Clg"; // token admin để gọi API

// 📥 Lấy dữ liệu doanh thu
async function fetchRevenue() {
  try {
    const res = await axios.get(`${API_BASE}/admin/revenue/history`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
    });
    return res.data;
  } catch (err) {
    console.error("❌ Lỗi khi lấy doanh thu:", err.response?.status, err.message);
    return [];
  }
}

// 📊 Lọc dữ liệu tháng trước
function filterLastMonth(data) {
  const now = new Date();
  const lastMonth = new Date(now.getFullYear(), now.getMonth() - 1, 1); // ngày 1 tháng trước
  const nextMonth = new Date(now.getFullYear(), now.getMonth(), 1);     // ngày 1 tháng này

  return data.filter(item => {
    const created = new Date(item.createdAt);
    return created >= lastMonth && created < nextMonth;
  });
}

// 📊 Gom dữ liệu theo ngày và tháng
function processRevenue(data) {
  const byDay = {};
  const byMonth = {};

  data.forEach((item) => {
    const date = new Date(item.createdAt);
    const dayKey = `${date.getFullYear()}-${(date.getMonth() + 1)
      .toString()
      .padStart(2, "0")}-${date.getDate().toString().padStart(2, "0")}`;
    const monthKey = `${date.getFullYear()}-${(date.getMonth() + 1)
      .toString()
      .padStart(2, "0")}`;

    byDay[dayKey] = (byDay[dayKey] || 0) + item.amount;
    byMonth[monthKey] = (byMonth[monthKey] || 0) + item.amount;
  });

  return { byDay, byMonth };
}

// 📤 Xuất Excel
function exportExcel({ byDay, byMonth }) {
  const wb = XLSX.utils.book_new();

  // Sheet doanh thu theo ngày
  const daySheet = XLSX.utils.json_to_sheet(
    Object.entries(byDay).map(([date, amount]) => ({ Ngay: date, DoanhThu: amount }))
  );
  XLSX.utils.book_append_sheet(wb, daySheet, "TheoNgay");

  // Sheet doanh thu theo tháng
  const monthSheet = XLSX.utils.json_to_sheet(
    Object.entries(byMonth).map(([month, amount]) => ({ Thang: month, DoanhThu: amount }))
  );
  XLSX.utils.book_append_sheet(wb, monthSheet, "TheoThang");

  const fileName = `DoanhThu_ThangTruoc_${new Date().toISOString().slice(0, 10)}.xlsx`;
  XLSX.writeFile(wb, fileName);
  return fileName;
}

// 📩 Gửi file Telegram
async function sendTelegram(filePath) {
  try {
    const form = new FormData();
    form.append("chat_id", CHAT_ID);
    form.append("document", fs.createReadStream(filePath));
    form.append("caption", `📊 Báo cáo doanh thu hệ thống tháng trước (${new Date().toLocaleDateString()})`);

    await axios.post(`https://api.telegram.org/bot${BOT_TOKEN}/sendDocument`, form, {
      headers: form.getHeaders(),
    });

    console.log("✅ Đã gửi file lên Telegram!");
  } catch (err) {
    console.error("❌ Lỗi khi gửi Telegram:", err.message);
  }
}

// ⚡ Hàm chính
async function main() {
  let revenueData = await fetchRevenue();
  revenueData = filterLastMonth(revenueData); // chỉ lấy tháng trước

  if (!revenueData.length) return console.log("⚠️ Không có dữ liệu doanh thu tháng trước.");

  const processed = processRevenue(revenueData);
  const filePath = exportExcel(processed);
  await sendTelegram(filePath);

  // ⚠️ Xóa file sau khi gửi
  fs.unlinkSync(filePath);
}

// Chạy trực tiếp
main();

