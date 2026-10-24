# LOOP — Live Wallpaper Hub

เตรียมโปรเจกต์สำหรับเก็บและสร้าง Live Wallpaper มีคลัง HoYoPlay สำหรับ Genshin, Zenless Zone Zero และ Honkai: Star Rail พร้อมโหมดเว็บกำหนดเอง หน้าเว็บแสดงตัวอย่าง/เก็บรายการ และ Python API เปิดหน้าเว็บจริงด้วย Chromium, จับฉากที่ประกอบจาก sprite, หาเฟรมต้น–ท้ายที่คล้ายกัน และส่งออกโปรเจกต์ Lively

## ใช้งานในเครื่อง

1. ติดตั้ง Python 3.10 ขึ้นไป
2. ดับเบิลคลิก `Start Hub.bat` แล้วรอให้ติดตั้ง dependencies ครั้งแรก
3. เปิด `http://127.0.0.1:8000`
4. เลือกเกมจากคลัง HoYoPlay หรือสลับไป “เว็บอื่น / ฉาก Sprite” แล้วใส่ URL หน้าเว็บ
5. เลือกวิดีโอ, Canvas หรือฉาก DOM ที่ระบบพบ ถ้า auto-detect เลือกฉากผิด ให้ระบุ CSS selector ของฉากหลังเอง
6. ตรวจช่วง Loop ที่ระบบแนะนำ ปรับจุดเริ่ม/จบได้ แล้วเพิ่มเข้าคลังหรือดาวน์โหลด ZIP

ไฟล์ ZIP มี `index.html`, `LivelyInfo.json` และสื่อที่จับได้ นำไปแตกไฟล์แล้วเพิ่มโฟลเดอร์ใน Lively Wallpaper

## การวิเคราะห์ Loop

Backend อ่านวิดีโอเป็นตัวอย่าง 6 เฟรมต่อวินาที ย่อเฟรมเป็น 48×27 ระดับเทา แล้วเปรียบเทียบความต่างของเฟรมคู่ที่ห่างกันอย่างน้อยประมาณหนึ่งวินาที จากคู่เฟรมที่คะแนนใกล้ที่สุด ระบบเลือกช่วงสั้นที่คล้ายกันเป็นจุดเริ่มต้นของ Loop เพื่อหลีกเลี่ยงการแนะนำหลายรอบต่อกัน ผู้ใช้ดูตัวอย่างแล้วปรับจุดเริ่ม/จบได้ คะแนนเป็นความคล้ายเชิงภาพเบื้องต้น ไม่ได้รับรองว่าทุกเฟรมจะต่อเนียน เพราะไม่ได้วิเคราะห์ motion vector และฉากที่แทบหยุดนิ่งอาจทำให้ระบบเลือกช่วงสั้นเกินไป

คลิปตัวอย่างที่ดึงไว้ยาว 3.00 วินาที ผลวิเคราะห์เบื้องต้นแนะนำช่วง 0.000–2.833 วินาที โดยเฟรมหัว/ท้ายคล้ายกัน 99.85% ควรลองเล่นและปรับจุดจริงก่อนใช้

วิดีโอจากคลังถูกดาวน์โหลดลง `data/media/` และแสดงจากไฟล์ในโปรเจกต์เอง Canvas จะถูกบันทึกเป็น WebM ส่วนฉาก DOM/Sprite จะจับ element ที่เลือกเป็น MP4 โดยกำหนดเวลาจับได้ 12, 20 หรือ 30 วินาที จากนั้นวิเคราะห์จุดวนด้วยเฟรมที่ได้ ภาพ/Texture ที่นิ่งไม่มีข้อมูลเวลาให้วิเคราะห์เป็น Loop

รายการเกมในคลังโหลดสดจาก JSON ของ HoYoPlay ผ่าน backend โดยเลือกเฉพาะ video entry ของเกมและ region ที่เลือก ไม่ฝังรายการ URL สื่อเฉพาะชิ้นไว้ในหน้าเว็บ โหมดกำหนดเองจะแยกวิดีโอ/Canvas ออกจากฉาก DOM ที่ประกอบจาก sprite แล้วจับเฉพาะ element ที่เลือกเป็นวิดีโอ เพื่อไม่ติดเมนูเว็บส่วนอื่นมาด้วย

## GitHub และ deployment

- Repository: [CAtlas0784/web-livewallpaper](https://github.com/CAtlas0784/web-livewallpaper)
- Gitflow: `main` เป็น production, `develop` เป็นงานรวม; แตก `feature/<ชื่อ>` จาก `develop` แล้ว PR กลับ `develop`; เตรียมรุ่นผ่าน `release/<รุ่น>` แล้ว PR เข้า `main` ก่อน merge กลับ `develop`; แก้ด่วนแตก `hotfix/<ชื่อ>` จาก `main` แล้ว PR เข้า `main` และ merge กลับ `develop`
- แนะนำตั้ง branch rules บน GitHub ให้ `main` และ `develop` รับการเปลี่ยนแปลงผ่าน Pull Request และบังคับให้ workflow ที่ต้องการผ่านก่อน merge; การตั้ง branch rules ต้องทำใน Repository Settings เพราะ git push ตั้งค่านี้ให้ไม่ได้
- GitHub Actions ใน `.github/workflows/pages.yml` deploy หน้าเว็บ `static/` ไป GitHub Pages เมื่อมีการ push เข้า `main`
- หลัง push ให้เปิด Repository Settings → Pages แล้วเลือก GitHub Actions เป็น Build and deployment source
- GitHub Pages เป็น static hosting จึงเปิด Chromium/ดึงสื่อจาก arbitrary URL หรือวิเคราะห์เฟรมเองไม่ได้ ต้องมี backend ที่รัน `api.py` แยกต่างหาก
- ถ้าตั้ง `HUB_API_URL` ใน GitHub repository variables, workflow จะฝัง API URL ลงหน้าเว็บอัตโนมัติ ตั้ง `LOOP_ALLOWED_ORIGINS` ฝั่ง API ให้ตรงกับ origin ของ Pages
- สำหรับคลังที่แชร์ร่วมกัน ต้อง deploy backend พร้อม persistent disk ที่เก็บโฟลเดอร์ `data/` ไว้ตลอด หากใช้ backend บนเครื่องผู้ใช้ คลังจะอยู่บนเครื่องนั้น

## Backend deployment

มี `Dockerfile` สำหรับทดลองบนเครื่องหรือบริการที่รองรับ Docker และ persistent volume ตั้งค่า `LOOP_DATA_DIR=/data`, `LOOP_ALLOWED_ORIGINS` เป็น origin ที่อนุญาต และผูก persistent volume ไปที่ `/data` ก่อนเปิดให้ใช้งานผ่านอินเทอร์เน็ต ตัว container ใช้ Playwright รุ่นเดียวกับ Python package และรันด้วยผู้ใช้ที่ไม่ใช่ root; งาน crawl ควรรัน Chromium ใน sandbox ด้วย seccomp ตามแนวทางของ Playwright

API จำกัด URL ให้เป็น HTTP/HTTPS สาธารณะและปิด private/localhost IP เพื่อลดความเสี่ยง SSRF อย่างไรก็ดี image นี้ยังเป็นต้นแบบและยังไม่มี authentication, rate limiting, quotas หรือ moderation อย่าเปิด backend นี้เป็นบริการสาธารณะจนกว่าจะเพิ่มการป้องกันเหล่านั้น

## ข้อจำกัด

- หน้าเว็บบางแห่งต้องเข้าสู่ระบบ, กันบอต, หรือประกอบภาพจากหลายเลเยอร์ซึ่งอาจสแกน/จับ Canvas ไม่ได้
- การวิเคราะห์จุด Loop ใช้ความคล้ายภาพระดับเฟรมและแนะนำตำแหน่งให้ตรวจ ไม่ได้วิเคราะห์ motion vector หรือเสียง
- การบันทึก Canvas จำกัด 2–60 วินาทีต่อครั้งใน API โดยหน้าเว็บมีตัวเลือก 12, 20 หรือ 30 วินาที
