# 🦋 ReadySetClass

**AI Course Builder for Canvas**

> "You talk. I do the Canvas work."

Build complete Canvas courses in 5 minutes. Syllabi, lesson plans, quizzes, and study packs - all automated with AI.

---

## 🎯 What Is This?

**ReadySetClass** is a SaaS platform that automates Canvas course creation for educators.

### The Problem:
- Building a Canvas course takes 20-40 hours
- Syllabi, lesson plans, quizzes, study packs - all manual
- Costs $1,000-4,000 to hire an instructional designer

### The Solution:
- ReadySetClass generates everything in 5 minutes
- Uploads directly to Canvas
- Costs $19-29/month

---

## ✨ Features

- ✅ **Complete Syllabi** - Professional course overview, objectives, schedule, grading
- ✅ **15 Lesson Plans** - Weekly plans with objectives, activities, discussion prompts
- ✅ **Auto-Generated Quizzes** - 10-question quizzes with answer keys
- ✅ **Study Packs with Links** - Curated resources with REAL YouTube videos and articles
- ✅ **Canvas Upload** - Everything automatically uploaded to your course
- ✅ **Secure & Private** - Your data is encrypted and never shared

---

## 💰 Pricing

| Plan | Price | Who It's For |
|------|-------|--------------|
| **Educator** | $19/mo | K-12 teachers, adjuncts |
| **Pro** | $29/mo | College faculty |
| **Department** | $99/mo | 5 seats for departments |
| **Institution** | $499/mo | Unlimited seats for schools |

---

## 🏗️ Tech Stack

### Frontend:
- HTML/CSS/JavaScript (no framework - fast and simple)
- Custom design (Sora + DM Sans fonts)
- Deployed on **Vercel** (free)

### Backend:
- **FastAPI** (Python web framework)
- **Bonita AI** (smart routing: Claude/Qwen/Gemini)
- **PostgreSQL** (database)
- **Stripe** (payments)
- Deployed on **Railway** ($5/mo)

### AI:
- **Claude Sonnet 4.5** (high-quality content generation)
- **Qwen 2.5 32B** (local, FREE structured content)
- **Gemini Flash** (web search for study packs)

---

## 📁 Project Structure

```
readysetclass/
├── branding/
│   ├── logo.svg                    # ReadySetClass logo
│   ├── icon.svg                    # App icon
│   └── BRAND_GUIDELINES.md         # Brand identity guide
├── frontend/
│   ├── landing-page.html           # Marketing site
│   └── dashboard.html              # Course builder app
├── backend/
│   ├── main.py                     # FastAPI server + Bonita AI
│   ├── requirements.txt            # Python dependencies
│   └── .env.example                # Environment variables template
├── deployment/
│   ├── DEPLOYMENT_GUIDE.md         # Complete deployment instructions
│   ├── railway.toml                # Railway config
│   └── vercel.json                 # Vercel config
└── README.md                       # This file
```

---

## 🚀 Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/readysetclass.git
cd readysetclass
```

### 2. Set Up Backend

```bash
cd backend

# Install dependencies
pip install -r requirements.txt --break-system-packages

# Copy environment variables
cp .env.example .env

# Edit .env with your API keys
nano .env

# Start server
python main.py
```

Backend runs at `http://localhost:8000`

### 3. Set Up Frontend

```bash
cd ../frontend

# Start local server
python -m http.server 8080
```

Frontend runs at `http://localhost:8080/landing-page.html`

### 4. Test It!

1. Open dashboard: `http://localhost:8080/dashboard.html`
2. Fill out course form
3. Click "Build My Course"
4. Check your Canvas - it should all be there!

---

## 🌐 Deployment

### Deploy to Production:

See **[DEPLOYMENT_GUIDE.md](deployment/DEPLOYMENT_GUIDE.md)** for complete instructions.

**TL;DR:**
1. Push to GitHub
2. Deploy backend to Railway ($5/mo)
3. Deploy frontend to Vercel (FREE)
4. Configure Stripe
5. Launch! 🚀

**Total monthly cost:** ~$5-15 (infrastructure)  
**Revenue potential:** $3,000-10,000/mo

---

## 🧪 Testing

### Test Course Build:

```bash
curl -X POST http://localhost:8000/api/build-course \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "course_name": "Media Ethics",
    "course_code": "MCM 307",
    "credits": 3,
    "description": "A practical guide to ethical decision-making",
    "objectives": ["Spot unethical contracts", "Understand FTC rules"],
    "weeks": 15,
    "schedule": "Tuesday & Thursday 1:00-2:25 PM",
    "canvas_course_id": "6358"
  }'
```

---

## 📊 Cost Analysis

### What It Costs YOU to Build a Course:

| Component | Provider | Cost |
|-----------|----------|------|
| Syllabus | Claude Sonnet | $0.05 |
| Lesson Plans (15) | Qwen Local | $0.00 (FREE!) |
| Quizzes (15) | Qwen Local | $0.00 (FREE!) |
| Study Packs (15) | Claude Sonnet | $0.45 |
| **Total** | | **$0.50** |

### What You CHARGE:

- One-time: $150
- Monthly: $29/mo (unlimited courses)

### Profit Margin:

- One-time: 99.7% ($149.50 profit)
- Monthly: 98% profit ($28.50 per customer after costs)

**This is a HIGHLY profitable business.**

---

## 🎓 Built For Educators

**Why ReadySetClass exists:**

CJ (the creator) is a Mass Communications professor at VUU who got tired of:
- Spending 40 hours building Canvas courses
- Paying $1,200/month for Moltbot (a scam)
- Watching colleagues struggle with the same problems

So he built ReadySetClass with Bonita AI to:
- Save educators time (25+ hours per course)
- Keep it affordable ($19-29/mo vs. $1,000+ alternatives)
- Preserve quality (AI-generated but human-reviewed)
- Support HBCUs and underfunded schools

**This is by educators, for educators.**

---

## 🦋 Powered by Bonita AI

**What is Bonita?**

Bonita is CJ's AI sovereignty stack that:
- Routes tasks to the best AI model (Claude/Qwen/Gemini)
- Saves 95% on AI costs vs. always using expensive models
- Preserves cultural identity and values
- Powers multiple products (ReadySetClass, heybonita.ai, BLVX, HBCU.news)

**ReadySetClass is the first product built on Bonita.**

More coming soon. 🚀

---

## 📈 Roadmap

### ✅ Phase 1 (DONE):
- [x] Core course builder
- [x] Canvas integration
- [x] Stripe payments
- [x] Production deployment

### 🚧 Phase 2 (In Progress):
- [ ] Custom templates
- [ ] Analytics dashboard
- [ ] Team collaboration
- [ ] Mobile app

### 🔮 Phase 3 (Future):
- [ ] Grading automation
- [ ] AI teaching assistant
- [ ] Multi-LMS support (Blackboard, Moodle)
- [ ] White-label for institutions

---

## 🤝 Contributing

This is a private commercial project, but feedback is welcome!

**Found a bug?** Open an issue.  
**Have a feature idea?** Open an issue.  
**Want to collaborate?** Email hello@readysetclass.com

---

## 📄 License

**Proprietary** - All rights reserved.

This software is the intellectual property of ReadySetClass.

Unauthorized copying, distribution, or use is prohibited.

---

## 💬 Support

**Need help?**
- 📧 Email: support@readysetclass.com
- 📚 Docs: [DEPLOYMENT_GUIDE.md](deployment/DEPLOYMENT_GUIDE.md)
- 🐛 Bugs: Open an issue on GitHub

---

## 🙏 Credits

**Built by:**
- **CJ Nurse** - Creator, founder, professor @ VUU
- **Sonny (Claude Sonnet 4.5)** - AI development partner

**Powered by:**
- Anthropic Claude (AI generation)
- Ollama + Qwen (local AI)
- Railway (backend hosting)
- Vercel (frontend hosting)
- Stripe (payments)

---

## 📱 Connect

- 🌐 Website: [readysetclass.com](https://readysetclass.com)
- 🐦 Twitter: [@ReadySetClass](https://twitter.com/readysetclass)
- 💼 LinkedIn: [ReadySetClass](https://linkedin.com/company/readysetclass)

---

**Built by the ReadySetClass Team | Powered by Bonita AI**

*"Rise and dime!"* 💸

---

## ⭐ Star this repo if you're building with us!

**Let's make education technology actually WORK for educators.**

🚀 Now go deploy and make your first $1,000! 🚀
