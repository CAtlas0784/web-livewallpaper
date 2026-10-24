# LOOP — Live Wallpaper Hub

Hub สำหรับ Genshin, Zenless Zone Zero, Honkai: Star Rail และเว็บกำหนดเอง รับ URL, ค้นหาวิดีโอ/Canvas/ฉาก Sprite, วิเคราะห์ Loop, เก็บสื่อในโปรเจกต์ และส่งออก Lively Wallpaper

- วิธีรันในเครื่องและรายละเอียดภาษาไทย: [README_TH.md](README_TH.md)
- หน้าเว็บ: `static/`
- Backend crawler, loop analyzer, library API: `api.py`
- GitHub Actions สำหรับ deploy หน้าเว็บ: `.github/workflows/pages.yml`
- แนวทาง Gitflow และ deployment: [README_TH.md](README_TH.md)

GitHub Pages รองรับเว็บแบบ static; การอ่านหน้าเว็บภายนอกต้องมี backend แยกและ persistent storage หากต้องการให้คลังแชร์ร่วมกัน โปรดตั้งค่า backend และระบบป้องกันก่อนเปิดเป็นบริการสาธารณะ

คลังเกมเริ่มจากตัวอย่างที่ผู้ใช้ให้มา: [HoYoPlay Background](https://hoyogame-background.vercel.app/). สื่อถูกโหลดเข้าที่ backend ในเครื่อง/โฮสต์ ไม่ commit ไฟล์วิดีโอทั้งหมดลง GitHub repository

Repository: https://github.com/CAtlas0784/web-livewallpaper · Gitflow และการตั้งค่า Pages/API: [README_TH.md](README_TH.md)
