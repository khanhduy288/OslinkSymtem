// oslinkTool.js
// Mô phỏng tool Oslink tích hợp vào backend

const { exec } = require("child_process");

class OslinkTool {
  constructor() {
    this.rooms = {}; // roomId -> { userId, devices }
  }

  // Mô phỏng mở Oslink app + tạo room
  async createRoom(userId) {
    return new Promise((resolve) => {
      const roomId = `ROOM-${Date.now()}`;
      this.rooms[roomId] = { userId, devices: [] };

      console.log(`📲 [OslinkTool] Created room ${roomId} for user ${userId}`);

      // Ví dụ giả lập mở app Oslink
      exec(`echo "Open Oslink App -> Create room ${roomId}"`, (err) => {
        if (err) console.error(err);
      });

      resolve(roomId);
    });
  }

  // Add thiết bị vào room
  async addDeviceToRoom(roomId, deviceName) {
    return new Promise((resolve, reject) => {
      if (!this.rooms[roomId]) return reject("Room not found!");

      this.rooms[roomId].devices.push(deviceName);
      console.log(`🖥️ [OslinkTool] Added device ${deviceName} to ${roomId}`);

      exec(`echo "Add device ${deviceName} to room ${roomId}"`, (err) => {
        if (err) console.error(err);
      });

      resolve(true);
    });
  }

  // Xoá thiết bị
  async removeDeviceFromRoom(roomId, deviceName) {
    return new Promise((resolve, reject) => {
      if (!this.rooms[roomId]) return reject("Room not found!");

      this.rooms[roomId].devices = this.rooms[roomId].devices.filter(
        (d) => d !== deviceName
      );
      console.log(`🗑️ [OslinkTool] Removed device ${deviceName} from ${roomId}`);

      exec(`echo "Remove device ${deviceName} from room ${roomId}"`, (err) => {
        if (err) console.error(err);
      });

      resolve(true);
    });
  }

  // Xoá room
  async deleteRoom(roomId) {
    return new Promise((resolve, reject) => {
      if (!this.rooms[roomId]) return reject("Room not found!");

      delete this.rooms[roomId];
      console.log(`❌ [OslinkTool] Deleted room ${roomId}`);

      exec(`echo "Delete room ${roomId}"`, (err) => {
        if (err) console.error(err);
      });

      resolve(true);
    });
  }
}

module.exports = new OslinkTool();
