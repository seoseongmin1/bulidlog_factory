# 공장 에너지 에이전트

철강 공장의 전력 데이터를 자연어로 분석하는 AI 에이전트입니다. "12월 16일 일요일에 왜 전력을 많이 썼을까?"라고 물으면 에이전트가 필요한 분석 도구를 골라 실행하고, 그래프와 함께 사실과 가설을 구분해 답합니다. 기획 문서는 [docs](docs)에 있습니다.

| 기능 | 내용 |
|---|---|
| 대시보드 | 연간 사용량, 최대 수요전력, 역률, CO2와 월별·시간대별·날짜별 그래프 |
| 이상한 날 | 주변 같은 요일 유형과 비교해 평소와 가장 다른 8일. 날짜를 누르면 "하루 보기"로 옮겨 그날의 보고서가 열립니다. |
| 일일 보고서 | 날짜를 고르면 표로 보여 주고, 버튼으로 `.md` 파일로 저장합니다. |
| AI 에이전트 | 평소 말투로 질문하면 분석 도구 13개(경보 예측 3개, 역률 개선 계산 1개 포함) 중 필요한 것을 골라 답합니다. |
| 한눈에 보기 | 생산직용. 날짜를 고르면 질문 없이 상태 신호등(좋음·주의·이상), 쉬운 세 줄 요약, 할 일이 나옵니다. 숫자와 상태는 계산으로 정하고 AI는 문장만 다듬습니다. |
| 경보 예측 | 2018년의 한 시각을 "지금"으로 보고, 1시간 안에 사용량(408.1kW 초과)이나 역률(85% 미만, 09~22시)이 기준을 넘을 확률과 위험 정도(좋음·주의·위험)를 머신러닝으로 계산합니다. 할 일과 지난 채점 결과, 하루 경보 타임라인(경보가 실제 초과보다 몇 분 먼저 켜졌는지)을 함께 보여 줍니다. AI는 쓰지 않습니다. |
| 감시 재생 | 2018년의 하루를 15분씩 흘려보내며, 그때 경보가 언제 켜지고 실제 초과보다 몇 분 먼저 알렸는지, 헛경보는 언제였는지를 알림으로 보여 줍니다. 실시간 데이터가 아니라 기록 재생입니다. AI는 쓰지 않습니다. |
| 경보 이유와 대응 기록 | 주의·위험 경보가 왜 울렸는지 이유를 보여 주고, 작업자가 조치함·무시함·오경보를 누르면 이 컴퓨터의 파일(`backend/local/`)에 기록해 통계와 "문턱을 올리면 어떻게 되는지"를 보여 줍니다. 기준은 자동으로 바뀌지 않습니다. |
| 역률 개선 계산기 | 기간과 목표 역률(85·90·95%)을 고르면 09~22시 역률을 목표까지 올리는 자동 역률 조정기 용량(kVar)을 추천하고, 같은 용량의 고정 콘덴서가 과보상으로 오히려 나빠지는지 비교합니다. 금액은 계산하지 않습니다. |

화면 왼쪽은 위쪽 탭(개요, 하루 보기, 이상한 날, 경보 예측, 감시 재생, 역률 개선)으로 나눠 하나씩 보여 주고, 오른쪽 AI 대화는 늘 보입니다.

## 구성

```
화면 (React, frontend/)  ──/api──▶  서버 (FastAPI, backend/)  ──▶  데이터베이스 (database/)
                                       └──▶ OpenAI API
```

- **화면:** 데이터베이스에 직접 접속하지 않습니다. 서버만 접속하므로 접속 정보가 화면에 노출되지 않습니다.
- **데이터베이스:** 원본 전력 데이터 35,040건만 넣습니다. 질문, 대화, 보고서는 저장하지 않습니다.
  - `backend/.env`의 `DATABASE_URL`(Supabase Session pooler 주소)로 접속합니다. 없으면 SQLite로 넘어가지 않고 오류와 확인할 항목을 알려 줍니다.
  - 자동 테스트만 임시 SQLite를 씁니다.
- **계산:** 모든 수치는 서버의 분석 코드가 계산하고, AI 모델(OpenAI)은 결과를 해석합니다. 그래프도 계산 결과로만 그립니다.

## 처음 한 번 (Windows PowerShell)

Python 3.15와 Node.js 26에서 확인했습니다.

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env
cd ..\frontend
npm install
```

`backend/.env`에 [OpenAI API 키](https://platform.openai.com/api-keys)를 넣으세요. 모델은 기본 `gpt-5`이고, `OPENAI_MODEL`로 바꿀 수 있습니다. 키는 사용한 만큼 요금이 나오며 다른 사람에게 보여 주면 안 됩니다. 키가 없어도 대시보드, 이상한 날, 보고서는 작동합니다.

```
OPENAI_API_KEY=발급받은_키
```

## 실행

```powershell
.\start.cmd
```

브라우저에서 http://localhost:8000 을 엽니다. 이 명령은 다음을 알아서 합니다.

1. 화면이 아직 빌드되지 않았으면 빌드합니다.
2. 데이터베이스에 전력 데이터가 없으면 `database/steel_industry_data.csv`에서 넣습니다.
3. 서버를 켭니다. 서버가 빌드된 화면도 함께 제공합니다.

포트를 바꾸려면 `.\start.cmd -Port 8001`을 씁니다.

## 개발할 때

화면 코드를 고치면서 바로 보려면 터미널 두 개를 씁니다.

```powershell
# 터미널 1: 서버
cd backend
.venv\Scripts\python -m app.loader --if-missing
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000

# 터미널 2: 화면 (http://localhost:5173)
cd frontend
npm run dev
```

## 테스트

```powershell
cd backend;  .venv\Scripts\python -m pytest     # 서버 (임시 SQLite만 씁니다)
cd frontend; npm test                            # 화면
```

경보 예측의 9~12월 채점 결과는 다음 명령으로 다시 계산합니다(약 5초). 결과 표는 [spec.md](docs/spec.md) 기능 6에 있습니다.

```powershell
cd backend; .venv\Scripts\python -m app.forecast
```

서버 테스트는 가짜 OpenAI 서버를 띄워 실제 SDK를 통과시킵니다. **실제 AI 모델의 답변 품질은 평가하지 않습니다.**

## Supabase로 바꾸기

1. [Supabase](https://supabase.com/)에서 프로젝트를 만들고 데이터베이스 비밀번호를 정합니다.
2. 프로젝트 상단 **Connect > Session pooler**의 연결 문자열을 `backend/.env`에 `DATABASE_URL=...`로 넣습니다. 자세한 형식은 `backend/.env.example`에 있습니다.
3. 데이터를 넣습니다. 표를 만들고, 외부에서 이 표를 읽거나 바꾸지 못하도록 행 수준 보안(RLS)도 자동으로 켭니다.
   ```powershell
   cd backend
   .venv\Scripts\python -m app.loader
   ```
4. 서버를 다시 켜면 Supabase를 씁니다. 무료 플랜은 오래 쓰지 않으면 프로젝트가 일시 중지될 수 있으니, 시연 전에 확인하세요.

기존 `database/energy.db` 파일은 더 이상 쓰지 않지만 지우지 않습니다. 그 안의 데이터를 옮기는 기능은 없고, 원본 CSV에서 새로 넣습니다.

## 한계

- 2018년 과거 데이터이며 실시간 연동이 없습니다.
- 공장 전체 전력만 있어 설비별 원인을 특정할 수 없습니다. 에이전트는 원인을 단정하지 않고 가능성으로 말합니다.
- 전기요금 데이터가 없어 금액을 계산하지 않습니다.
- 로그인과 사용량 제한이 없는 로컬 시연용입니다. 인터넷에 공개하면 다른 사람이 내 API 키로 요금을 쓸 수 있습니다.

## 데이터 출처

[Steel Industry Energy Consumption](https://archive.ics.uci.edu/dataset/851/steel+industry+energy+consumption), UCI Machine Learning Repository. DAEWOO Steel Co. Ltd (광양). [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). 원본 파일을 변경 없이 `database/`에 포함했습니다.
