# Telegram Credit Link-Exchange Bot

Daraz (বা যেকোনো) লিংক পোস্ট করার এবং ক্লিক করে ক্রেডিট আয় করার বট।

## নিয়মকানুন (Logic)

- নতুন user শুরুতে **0 credit** পাবে।
- লিংক পোস্ট করার সময় user বলে দেয় **কত credit খরচ করবে** — সেই সংখ্যাই হলো
  ঠিক কতজন **ইউনিক মানুষ** এই লিংকটা দেখবে/ক্লিক করবে।
  - উদাহরণ: 5 credit দিয়ে পোস্ট করলে লিংকটা ঠিক 5 জন ভিন্ন মানুষের কাছে যাবে।
- প্রতিটা লিংক প্রতিটা মানুষের ফিডে **শুধু একবার** আসে — একই লিংক কারো কাছে
  দ্বিতীয়বার দেখানো হয় না, সবসময় ইউনিক/নতুন লিংক দেখানো হয়।
- যতজনের জন্য পোস্ট করা হয়েছিল, ততজন ক্লিক করে claim করে ফেললে লিংকটা
  স্বয়ংক্রিয়ভাবে সবার ফিড থেকে সরে যায় (আর কেউ দেখবে না)।
- নিজের পোস্ট করা লিংক নিজে "Earn Credit" এ দেখা/ক্লিক করা যাবে না।
- Menu তে ৩টা অপশন: **Account Info**, **Add Daraz Link**, **Earn Credit**।
- Account Info -> username, user id, credit balance, total clicks, total referrals, এবং একটা **রেফার লিংক**।
- **Referral System**: প্রতিটা ইউজারের একটা ইউনিক রেফার লিংক আছে (`https://t.me/BOTUSERNAME?start=ref<user_id>`)।
  কেউ প্রথমবার এই লিংক দিয়ে বটে জয়েন করলে, যে রেফার করেছে সে সাথে সাথে **+5 ক্রেডিট** পাবে।
  - একজন ইউজার শুধুমাত্র একবারই "নতুন" হতে পারে, তাই একই ইউজার বারবার ক্লিক করে referrer কে বারবার bonus দিতে পারবে না।
  - নিজেকে নিজে রেফার করা যাবে না (self-referral ব্লক করা আছে)।

## Setup

1. **BotFather** থেকে একটা bot বানিয়ে token নিন (Telegram এ `@BotFather` কে `/newbot` পাঠান)।

2. Dependencies install করুন:
   ```bash
   pip install -r requirements.txt
   ```

3. Token সেট করুন এবং বট রান করুন:
   ```bash
   export BOT_TOKEN="8851720934:AAG30uuRAA7H5jdAKjHOjyZemD6MFSK7ig4"
   python bot.py
   ```

4. Telegram এ আপনার বট খুলে `/start` চাপুন।

## Files

- `bot.py` — বটের মূল লজিক ও Telegram handlers
- `database.py` — SQLite database layer (users, links, clicks)
- `requirements.txt` — Python dependency
- বট প্রথমবার রান হলে `bot_data.db` নামে একটা SQLite ফাইল নিজে থেকেই তৈরি হবে

## সম্ভাব্য পরবর্তী upgrade (এখন নেই, চাইলে বলবেন)

- একই ইউজার বারবার claim করলে ban / cooldown সিস্টেম
- Admin panel (কারা কারা পোস্ট করেছে, কে কে click করেছে দেখার জন্য)
- একটা লিংকে একের বেশি click দরকার হলে (এখন default 1 click/link)
- Referral system (নতুন ইউজার আনলে বোনাস ক্রেডিট)
- Fake-click ঠেকাতে actual link-visit ভেরিফিকেশন (এখনটা honor-system ভিত্তিক — ইউজার নিজে বাটন চাপলেই credit পায়)
