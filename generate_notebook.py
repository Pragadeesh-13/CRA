"""
Generates: Complaint_Resolution_Agent.ipynb
Run      : python generate_notebook.py
Upload   : The produced .ipynb + complaints-deduplicated.csv to Kaggle
"""

import nbformat as nbf
import json

nb  = nbf.v4.new_notebook()
nb.metadata["kernelspec"] = {
    "display_name": "Python 3",
    "language": "python",
    "name": "python3"
}
nb.metadata["language_info"] = {"name": "python", "version": "3.10.0"}

cells = []

def md(src):  cells.append(nbf.v4.new_markdown_cell(src))
def code(src): cells.append(nbf.v4.new_code_cell(src))

# ─────────────────────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────────────────────
md("""# 🎫 Complaint Resolution Agent — Master Notebook
**Pipeline:** Customer Complaint → Preprocessing → TF-IDF Baseline → distilBERT Classification → NER → BERTopic → FAISS Similarity → XGBoost Resolution Time → Team Recommendation → Fusion Report

---
### 📋 Before Running
1. Upload `complaints-deduplicated.csv` as a **Kaggle Dataset** (Dataset → New → upload CSV)
2. Add it to this notebook via **Add Data** and note the path (update `DATA_PATH` in the Config cell)
3. Enable **GPU accelerator** (Settings → Accelerator → GPU T4 x2)
4. Enable **Internet** (Settings → Internet → On)
""")

# ─────────────────────────────────────────────────────────────
# SECTION 0: INSTALL & SETUP
# ─────────────────────────────────────────────────────────────
md("## ⚙️ Section 0 — Install & Setup")

md("""> **GPU Acceleration Notice:**
> - **Kaggle / Colab:** Ensure GPU is selected in Settings (Settings → Accelerator → GPU T4 x2).
> - **Local Users (RTX 5060 / 50xx / 40xx / 30xx):** Cell 0-A will automatically detect your physical NVIDIA GPU and install the CUDA-enabled PyTorch wheel (`cu128` for Blackwell RTX 5060) if it's currently running on CPU.""")

code("""\
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CELL 0-A  HARDWARE DETECTION & PYTORCH CUDA ENVIRONMENT SETUP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sys, os, subprocess

def detect_physical_gpu():
    names = []
    if os.name == 'nt':
        try:
            cmd = ['powershell', '-NoProfile', '-Command',
                   'Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name']
            out = subprocess.check_output(cmd, encoding='utf-8', stderr=subprocess.DEVNULL)
            for line in out.strip().splitlines():
                l = line.strip()
                if any(k in l.lower() for k in ['nvidia', 'geforce', 'rtx']):
                    names.append(l)
        except Exception:
            pass
    if not names:
        try:
            out = subprocess.check_output(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                                         encoding='utf-8', stderr=subprocess.DEVNULL)
            names = [l.strip() for l in out.strip().splitlines() if l.strip()]
        except Exception:
            pass
    return names

cuda_ready = False
try:
    import torch
    cuda_ready = torch.cuda.is_available()
except ImportError:
    torch = None

phys_gpus = detect_physical_gpu()

print("=" * 65)
print("  GPU ENVIRONMENT DIAGNOSTIC")
print("=" * 65)

if phys_gpus:
    print(f"Physical GPU detected : {', '.join(phys_gpus)}")
else:
    print("Physical GPU query    : Standard check (no direct NVIDIA PCI query)")

if torch:
    print(f"PyTorch version       : {torch.__version__}")
    print(f"CUDA available        : {cuda_ready}")
    if cuda_ready:
        print(f"Active Device         : {torch.cuda.get_device_name(0)}")
        print("Status                : [OK] PyTorch is directly utilizing your GPU!")
else:
    print("PyTorch               : Not installed yet")

if not cuda_ready:
    print("\\n" + "!" * 65)
    print("NOTICE: PyTorch is currently running on CPU!")
    if phys_gpus:
        print(f"Your system has {phys_gpus[0]}, but PyTorch lacks CUDA support.")
    print("!" * 65)

    is_50_series = any('50' in g for g in phys_gpus)
    cuda_tag = 'cu128' if is_50_series else 'cu126'

    print(f"\\nInstalling CUDA-enabled PyTorch ({cuda_tag}) for your GPU...")
    try:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--upgrade",
            "torch", "torchvision", "torchaudio",
            "--index-url", f"https://download.pytorch.org/whl/{cuda_tag}"
        ])
        print("\\n" + "=" * 65)
        print("INSTALLED SUCCESSFULLY!")
        print("ACTION REQUIRED: Restart your Jupyter Kernel now:")
        print("  Menu -> Kernel -> Restart Kernel")
        print("Then re-run the notebook — your RTX GPU will be fully active!")
        print("=" * 65)
    except Exception as e:
        print(f"Automatic installation encountered an error: {e}")
        print("Please run this command in your terminal / command prompt:")
        print(f"  pip install --upgrade torch torchvision torchaudio --index-url https://download.pytorch.org/whl/{cuda_tag}")
""")

code("""\
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CELL 0-B  Install extra packages
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import subprocess, sys, torch

def pip(*args):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *args])

# Core NLP/ML extras
pip("sentence-transformers", "bertopic", "umap-learn", "hdbscan")

# FAISS — prefer GPU build when CUDA is available
if torch.cuda.is_available():
    try:
        pip("faiss-gpu-cu12")
        print("faiss-gpu-cu12 installed")
    except Exception:
        pip("faiss-cpu")
        print("faiss-cpu installed (faiss-gpu unavailable)")
else:
    pip("faiss-cpu")
    print("faiss-cpu installed")

print("All packages ready.")
""")

code("""\
import os, re, json, warnings, pickle
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from tqdm.auto import tqdm

# Scikit-learn
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, classification_report,
    confusion_matrix, f1_score, mean_squared_error
)
from sklearn.pipeline import Pipeline

# PyTorch & HuggingFace
import torch
from torch.utils.data import Dataset, DataLoader
from transformers import (
    AutoTokenizer, AutoModelForSequenceClassification,
    get_linear_schedule_with_warmup, pipeline as hf_pipeline
)
from torch.optim import AdamW

# Sentence Transformers
from sentence_transformers import SentenceTransformer

# BERTopic
from bertopic import BERTopic
from umap import UMAP
from hdbscan import HDBSCAN

# FAISS
import faiss

# XGBoost
import xgboost as xgb

# spaCy
import spacy

# NLTK
import nltk
nltk.download('stopwords', quiet=True)
from nltk.corpus import stopwords

warnings.filterwarnings("ignore")
plt.style.use("seaborn-v0_8-whitegrid")

# ── GPU Diagnostics ──────────────────────────────────────
print(f"PyTorch  version : {torch.__version__}")
print(f"CUDA available   : {torch.cuda.is_available()}")
if torch.cuda.is_available():
    props = torch.cuda.get_device_properties(0)
    print(f"GPU name         : {props.name}")
    print(f"VRAM             : {props.total_memory / 1024**3:.1f} GB")
    print(f"CUDA version     : {torch.version.cuda}")
    print(f"SM capability    : {props.major}.{props.minor}")
else:
    print()
    print("WARNING: No CUDA GPU detected — training will run on CPU (much slower).")
    print("If you have an NVIDIA GPU, go back to Cell 0-A and reinstall PyTorch with CUDA support.")
""")

code("""\
import os, torch

# ─────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────

# Auto-detect environment: Kaggle vs Local
IS_KAGGLE   = os.path.exists("/kaggle/working")
OUTPUT_DIR  = "/kaggle/working" if IS_KAGGLE else "."
print(f"Environment : {'Kaggle' if IS_KAGGLE else 'Local'}")

# ── Data path ──
# Kaggle : keep as-is after adding dataset via Add Data
# Local  : change to your CSV path
DATA_PATH = (
    "/kaggle/input/complaint-resolution-agent/complaints-deduplicated.csv"
    if IS_KAGGLE else
    "complaints-deduplicated.csv"   # <- change this for local runs
)

# ── Models ──
BERT_MODEL_NAME  = "distilbert-base-uncased"
SBERT_MODEL_NAME = "all-MiniLM-L6-v2"

# ── Training hyperparameters ──
MAX_LEN      = 128
BATCH_SIZE   = 32    # lower to 16 if you hit OOM
EPOCHS       = 3
LR           = 2e-5
WARMUP_RATIO = 0.1
SEED         = 42

# ── Output paths ──
BERT_SAVE        = os.path.join(OUTPUT_DIR, "bert_category_classifier")
BASELINE_SAVE    = os.path.join(OUTPUT_DIR, "tfidf_logreg.pkl")
FAISS_INDEX_PATH = os.path.join(OUTPUT_DIR, "faiss_index.bin")
EMBEDDINGS_CACHE = os.path.join(OUTPUT_DIR, "sbert_embeddings.npy")
LABEL_MAP_PATH   = os.path.join(OUTPUT_DIR, "label_map.json")

TOPIC_SAMPLE = 50_000   # BERTopic subsample (set None to use all rows)

# ── Device ──
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

if DEVICE.type == "cuda":
    props = torch.cuda.get_device_properties(0)
    print(f"Device      : CUDA — {props.name}")
    print(f"VRAM        : {props.total_memory / 1024**3:.1f} GB")
    # RTX 5060 Blackwell has SM 12.x — auto batch size tuning
    if props.major >= 12:
        BATCH_SIZE = 64   # Blackwell handles larger batches well
        print(f"Blackwell GPU detected — bumping BATCH_SIZE to {BATCH_SIZE}")
    elif props.total_memory > 16 * 1024**3:
        BATCH_SIZE = 64
        print(f"Large VRAM detected — bumping BATCH_SIZE to {BATCH_SIZE}")
else:
    print("Device      : CPU  (training will be slow)")
    print("             -> Go back to Cell 0-A to enable GPU")

print(f"Batch size  : {BATCH_SIZE}")
print(f"Output dir  : {OUTPUT_DIR}")

os.makedirs(OUTPUT_DIR, exist_ok=True)
torch.manual_seed(SEED)
import numpy as np; np.random.seed(SEED)
""")

# ─────────────────────────────────────────────────────────────
# SECTION 1: DATA LOADING & EDA
# ─────────────────────────────────────────────────────────────
md("---\n## 📊 Section 1 — Data Loading & EDA")

code("""\
df = pd.read_csv(DATA_PATH)
print(f"Shape   : {df.shape}")
print(f"Columns : {df.columns.tolist()}")
df.head(3)
""")

code("""\
# Null summary
null_counts = df.isnull().sum()
null_counts = null_counts[null_counts > 0]
print("Columns with nulls:")
print(null_counts)
""")

code("""\
fig, axes = plt.subplots(1, 3, figsize=(20, 5))

# Category distribution
cat_counts = df['category'].value_counts()
axes[0].barh(cat_counts.index, cat_counts.values, color='steelblue')
axes[0].set_title("Category Distribution", fontsize=13)
axes[0].set_xlabel("Count")

# Team distribution
team_counts = df['assigned_team'].value_counts()
axes[1].bar(team_counts.index, team_counts.values, color='coral')
axes[1].set_title("Assigned Team Distribution", fontsize=13)
axes[1].tick_params(axis='x', rotation=30)

# Status distribution
status_counts = df['status'].value_counts()
axes[2].bar(status_counts.index, status_counts.values, color='mediumseagreen')
axes[2].set_title("Ticket Status Distribution", fontsize=13)
axes[2].tick_params(axis='x', rotation=30)

plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/eda_distributions.png", dpi=150)
plt.show()
""")

code("""\
# Text length analysis
df['word_count'] = df['issue_description'].str.split().str.len()
print(df['word_count'].describe().round(2))
print(f"\\n95th percentile : {df['word_count'].quantile(0.95):.0f} words")
print(f"99th percentile : {df['word_count'].quantile(0.99):.0f} words")

plt.figure(figsize=(10, 4))
plt.hist(df['word_count'], bins=50, color='steelblue', edgecolor='white')
plt.axvline(df['word_count'].quantile(0.95), color='red', linestyle='--', label='95th pct')
plt.title("Complaint Text Length Distribution (words)")
plt.xlabel("Word count")
plt.ylabel("Frequency")
plt.legend()
plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/text_length_dist.png", dpi=150)
plt.show()
""")

# ─────────────────────────────────────────────────────────────
# SECTION 2: PREPROCESSING
# ─────────────────────────────────────────────────────────────
md("---\n## 🧹 Section 2 — Preprocessing")

code("""\
GREETING_RE = re.compile(
    r'^(hello[,\\s]?|hi[,\\s]?|hi\\s+there[,\\s]?|hey[,\\s]?|good\\s+(morning|afternoon|evening)[,\\s]?'
    r'|greetings[,\\s]?|dear\\s+(support|team)[,\\s]?)',
    re.IGNORECASE
)
FILLER_RE = re.compile(
    r'(reaching out because|hoping you can help[:\\s]?|quick question\\s*[-–]?'
    r'|need some help here\\.?|been struggling with this\\.?|any help appreciated\\.?'
    r'|let me know what you need from me\\.?|thanks in advance\\.?|please advise\\.?)',
    re.IGNORECASE
)
STOP_WORDS = set(stopwords.words('english'))


def clean_bert(text: str) -> str:
    \"\"\"Light clean for BERT — preserve natural language structure.\"\"\"
    text = GREETING_RE.sub('', str(text))
    text = FILLER_RE.sub('', text)
    return re.sub(r'\\s+', ' ', text).strip().lower()


def clean_tfidf(text: str) -> str:
    \"\"\"Full clean for TF-IDF — remove stopwords + punctuation.\"\"\"
    text = clean_bert(text)
    text = re.sub(r'[^a-z0-9\\s]', ' ', text)
    return ' '.join(w for w in text.split() if w not in STOP_WORDS and len(w) > 2)


df['clean_bert']  = df['issue_description'].apply(clean_bert)
df['clean_tfidf'] = df['issue_description'].apply(clean_tfidf)

print("Sample:")
print(f"  Original : {df['issue_description'].iloc[0]}")
print(f"  BERT     : {df['clean_bert'].iloc[0]}")
print(f"  TF-IDF   : {df['clean_tfidf'].iloc[0]}")
""")

code("""\
# Label encoding
le = LabelEncoder()
df['label'] = le.fit_transform(df['category'])

LABEL_MAP = {int(i): cls for i, cls in enumerate(le.classes_)}
ID2LABEL   = LABEL_MAP
LABEL2ID   = {v: k for k, v in LABEL_MAP.items()}

with open(LABEL_MAP_PATH, 'w') as f:
    json.dump(LABEL_MAP, f, indent=2)

print("Label mapping:")
for k, v in LABEL_MAP.items():
    print(f"  {k:>2}: {v}")
""")

code("""\
# Stratified 80 / 10 / 10 split
train_df, temp_df = train_test_split(df, test_size=0.2, random_state=SEED, stratify=df['label'])
val_df,   test_df = train_test_split(temp_df, test_size=0.5, random_state=SEED, stratify=temp_df['label'])

print(f"Train : {len(train_df):,}")
print(f"Val   : {len(val_df):,}")
print(f"Test  : {len(test_df):,}")
""")

# ─────────────────────────────────────────────────────────────
# SECTION 3: TF-IDF BASELINE
# ─────────────────────────────────────────────────────────────
md("---\n## 📈 Section 3 — TF-IDF Baseline (Logistic Regression)")

code("""\
print("Training TF-IDF + Logistic Regression baseline...")

tfidf_pipeline = Pipeline([
    ('tfidf', TfidfVectorizer(
        max_features=50_000,
        ngram_range=(1, 2),
        sublinear_tf=True,
        min_df=2
    )),
    ('clf', LogisticRegression(
        C=1.0, max_iter=1000,
        multi_class='multinomial',
        solver='lbfgs',
        n_jobs=-1,
        random_state=SEED
    ))
])

tfidf_pipeline.fit(train_df['clean_tfidf'], train_df['label'])

val_preds_bl = tfidf_pipeline.predict(val_df['clean_tfidf'])
baseline_acc = accuracy_score(val_df['label'], val_preds_bl)
baseline_f1  = f1_score(val_df['label'], val_preds_bl, average='macro')

print(f"Val Accuracy : {baseline_acc:.4f}")
print(f"Val Macro F1 : {baseline_f1:.4f}")
print()
print(classification_report(val_df['label'], val_preds_bl, target_names=le.classes_))

with open(BASELINE_SAVE, 'wb') as f:
    pickle.dump(tfidf_pipeline, f)
print(f"Baseline model saved → {BASELINE_SAVE}")
""")

code("""\
# Confusion matrix — baseline
plt.figure(figsize=(12, 10))
cm = confusion_matrix(val_df['label'], val_preds_bl)
sns.heatmap(cm, annot=True, fmt='d', cmap='Blues',
            xticklabels=le.classes_, yticklabels=le.classes_)
plt.title("TF-IDF Baseline — Confusion Matrix", fontsize=14)
plt.xlabel("Predicted"); plt.ylabel("Actual")
plt.xticks(rotation=45, ha='right')
plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/baseline_confusion_matrix.png", dpi=150)
plt.show()
""")

# ─────────────────────────────────────────────────────────────
# SECTION 4: DISTILBERT FINE-TUNING
# ─────────────────────────────────────────────────────────────
md("---\n## 🤖 Section 4 — distilBERT Category Classifier")

code("""\
class ComplaintDataset(Dataset):
    def __init__(self, texts, labels, tokenizer, max_length=MAX_LEN):
        self.texts     = list(texts)
        self.labels    = list(labels)
        self.tokenizer = tokenizer
        self.max_len   = max_length

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, idx):
        enc = self.tokenizer(
            self.texts[idx],
            max_length=self.max_len,
            padding='max_length',
            truncation=True,
            return_tensors='pt'
        )
        return {
            'input_ids'     : enc['input_ids'].squeeze(0),
            'attention_mask': enc['attention_mask'].squeeze(0),
            'labels'        : torch.tensor(self.labels[idx], dtype=torch.long)
        }
""")

code("""\
num_labels = len(le.classes_)
tokenizer  = AutoTokenizer.from_pretrained(BERT_MODEL_NAME)

model = AutoModelForSequenceClassification.from_pretrained(
    BERT_MODEL_NAME,
    num_labels=num_labels,
    id2label=ID2LABEL,
    label2id=LABEL2ID
).to(DEVICE)

# Safe loader workers for Windows and Linux
num_workers = 0 if os.name == 'nt' else 2
pin_mem     = torch.cuda.is_available()

train_ds = ComplaintDataset(train_df['clean_bert'].values, train_df['label'].values, tokenizer)
val_ds   = ComplaintDataset(val_df['clean_bert'].values,   val_df['label'].values,   tokenizer)
test_ds  = ComplaintDataset(test_df['clean_bert'].values,  test_df['label'].values,  tokenizer)

train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True,  num_workers=num_workers, pin_memory=pin_mem)
val_loader   = DataLoader(val_ds,   batch_size=BATCH_SIZE, shuffle=False, num_workers=num_workers, pin_memory=pin_mem)
test_loader  = DataLoader(test_ds,  batch_size=BATCH_SIZE, shuffle=False, num_workers=num_workers, pin_memory=pin_mem)

print(f"Train batches : {len(train_loader)}")
print(f"Val batches   : {len(val_loader)}")
print(f"Parameters    : {sum(p.numel() for p in model.parameters()):,}")
print(f"Workers       : {num_workers} | Pin Memory: {pin_mem}")
""")

code("""\
total_steps  = len(train_loader) * EPOCHS
warmup_steps = int(total_steps * WARMUP_RATIO)

optimizer = AdamW(model.parameters(), lr=LR, weight_decay=0.01)
scheduler = get_linear_schedule_with_warmup(
    optimizer,
    num_warmup_steps=warmup_steps,
    num_training_steps=total_steps
)

# PyTorch AMP Scaler for GPU acceleration (RTX 50-series Tensor Cores)
use_cuda = torch.cuda.is_available()
scaler   = torch.amp.GradScaler('cuda', enabled=use_cuda)


def train_epoch(model, loader):
    model.train()
    total_loss = correct = total = 0
    for batch in tqdm(loader, desc="  Training", leave=False):
        ids  = batch['input_ids'].to(DEVICE)
        mask = batch['attention_mask'].to(DEVICE)
        lbls = batch['labels'].to(DEVICE)
        optimizer.zero_grad()

        if use_cuda:
            with torch.amp.autocast('cuda'):
                out  = model(input_ids=ids, attention_mask=mask, labels=lbls)
                loss = out.loss
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimizer)
            scaler.update()
        else:
            out  = model(input_ids=ids, attention_mask=mask, labels=lbls)
            loss = out.loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

        scheduler.step()
        total_loss += loss.item()
        correct    += (out.logits.argmax(1) == lbls).sum().item()
        total      += len(lbls)
    return total_loss / len(loader), correct / total


def eval_epoch(model, loader):
    model.eval()
    total_loss = correct = total = 0
    all_preds, all_labels = [], []
    with torch.no_grad():
        for batch in tqdm(loader, desc="  Evaluating", leave=False):
            ids  = batch['input_ids'].to(DEVICE)
            mask = batch['attention_mask'].to(DEVICE)
            lbls = batch['labels'].to(DEVICE)
            if use_cuda:
                with torch.amp.autocast('cuda'):
                    out = model(input_ids=ids, attention_mask=mask, labels=lbls)
            else:
                out = model(input_ids=ids, attention_mask=mask, labels=lbls)

            total_loss += out.loss.item()
            preds       = out.logits.argmax(1)
            correct    += (preds == lbls).sum().item()
            total      += len(lbls)
            all_preds.extend(preds.cpu().numpy())
            all_labels.extend(lbls.cpu().numpy())
    return total_loss / len(loader), correct / total, all_preds, all_labels
""")

code("""\
history = {'train_loss': [], 'train_acc': [], 'val_loss': [], 'val_acc': []}
best_val_acc = 0.0

for epoch in range(1, EPOCHS + 1):
    print(f"\\nEpoch {epoch}/{EPOCHS}")
    tr_loss, tr_acc = train_epoch(model, train_loader)
    vl_loss, vl_acc, vl_preds, vl_labels = eval_epoch(model, val_loader)

    history['train_loss'].append(tr_loss)
    history['train_acc'].append(tr_acc)
    history['val_loss'].append(vl_loss)
    history['val_acc'].append(vl_acc)

    print(f"  Train Loss={tr_loss:.4f}  Acc={tr_acc:.4f}")
    print(f"  Val   Loss={vl_loss:.4f}  Acc={vl_acc:.4f}")

    if vl_acc > best_val_acc:
        best_val_acc = vl_acc
        model.save_pretrained(BERT_SAVE)
        tokenizer.save_pretrained(BERT_SAVE)
        print(f"  ✅ Best model saved  (val_acc={vl_acc:.4f})")

print(f"\\nBest val accuracy: {best_val_acc:.4f}")
""")

code("""\
# Training curves
fig, axes = plt.subplots(1, 2, figsize=(14, 5))
axes[0].plot(history['train_loss'], 'o-', label='Train')
axes[0].plot(history['val_loss'],   'o-', label='Val')
axes[0].set_title("Loss per Epoch"); axes[0].set_xlabel("Epoch"); axes[0].legend()
axes[1].plot(history['train_acc'],  'o-', label='Train')
axes[1].plot(history['val_acc'],    'o-', label='Val')
axes[1].set_title("Accuracy per Epoch"); axes[1].set_xlabel("Epoch"); axes[1].legend()
plt.suptitle("distilBERT Training History", fontsize=14)
plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/bert_training_curves.png", dpi=150)
plt.show()
""")

code("""\
# Test set evaluation
print("Loading best checkpoint for test evaluation...")
best_model = AutoModelForSequenceClassification.from_pretrained(BERT_SAVE).to(DEVICE)
_, test_acc, test_preds, test_labels = eval_epoch(best_model, test_loader)

bert_f1 = f1_score(test_labels, test_preds, average='macro')
print(f"\\nTest Set Results:")
print(f"  Accuracy : {test_acc:.4f}")
print(f"  Macro F1 : {bert_f1:.4f}")
print()
print(classification_report(test_labels, test_preds, target_names=le.classes_))

plt.figure(figsize=(12, 10))
cm = confusion_matrix(test_labels, test_preds)
sns.heatmap(cm, annot=True, fmt='d', cmap='Greens',
            xticklabels=le.classes_, yticklabels=le.classes_)
plt.title("distilBERT — Test Set Confusion Matrix", fontsize=14)
plt.xlabel("Predicted"); plt.ylabel("Actual")
plt.xticks(rotation=45, ha='right')
plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/bert_confusion_matrix.png", dpi=150)
plt.show()
""")

# ─────────────────────────────────────────────────────────────
# SECTION 5: NER
# ─────────────────────────────────────────────────────────────
md("---\n## 🔍 Section 5 — Named Entity Recognition (NER)")

code("""\
!python -m spacy download en_core_web_sm -q
""")

code("""\
nlp_ner = spacy.load("en_core_web_sm")

ruler = nlp_ner.add_pipe("entity_ruler", before="ner")
ruler.add_patterns([
    # Products
    {"label": "PRODUCT", "pattern": "Mobile App"},
    {"label": "PRODUCT", "pattern": "Desktop Client"},
    {"label": "PRODUCT", "pattern": "Web Portal"},
    {"label": "PRODUCT", "pattern": "API Platform"},
    {"label": "PRODUCT", "pattern": "Payment Gateway"},
    {"label": "PRODUCT", "pattern": "Analytics Suite"},
    # Issue keywords
    {"label": "ISSUE_TYPE", "pattern": "two-factor authentication"},
    {"label": "ISSUE_TYPE", "pattern": [{"LOWER": "2fa"}]},
    {"label": "ISSUE_TYPE", "pattern": "API documentation"},
    {"label": "ISSUE_TYPE", "pattern": "bulk export"},
    {"label": "ISSUE_TYPE", "pattern": "data sync"},
    {"label": "ISSUE_TYPE", "pattern": "subscription renewal"},
    {"label": "ISSUE_TYPE", "pattern": "double charge"},
])

REF_RE = re.compile(r'\\b(?:order|ticket|ref(?:erence)?|case|id)\\s*[#:\\-]?\\s*\\d+\\b', re.IGNORECASE)


def extract_entities(text: str) -> dict:
    doc = nlp_ner(str(text))
    ents = {'products': [], 'issue_types': [], 'dates': [], 'locations': [], 'references': []}
    for ent in doc.ents:
        if ent.label_ == "PRODUCT":
            ents['products'].append(ent.text)
        elif ent.label_ == "ISSUE_TYPE":
            ents['issue_types'].append(ent.text)
        elif ent.label_ in ("DATE", "TIME"):
            ents['dates'].append(ent.text)
        elif ent.label_ in ("GPE", "LOC"):
            ents['locations'].append(ent.text)
    ents['references'] = REF_RE.findall(text)
    for k in ents:
        ents[k] = list(set(ents[k]))
    return ents


# Demo
print("NER demos:")
samples = df['issue_description'].sample(5, random_state=SEED).tolist()
for s in samples:
    print(f"  Text     : {s[:100]}")
    print(f"  Entities : {extract_entities(s)}")
    print()
""")

code("""\
# Apply NER to full dataset
print(f"Extracting entities from {len(df):,} complaints...")
ner_results = []
BATCH = 500
for i in tqdm(range(0, len(df), BATCH)):
    for txt in df['issue_description'].iloc[i:i+BATCH]:
        ner_results.append(extract_entities(txt))

df['entities'] = ner_results
print("Done. Sample:")
df[['issue_description', 'entities']].head(3)
""")

# ─────────────────────────────────────────────────────────────
# SECTION 6: BERTOPIC
# ─────────────────────────────────────────────────────────────
md("---\n## 📚 Section 6 — Topic Modeling (BERTopic)")

code("""\
print("Loading Sentence-BERT model...")
sbert = SentenceTransformer(SBERT_MODEL_NAME, device=str(DEVICE))

topic_df   = df.sample(n=min(TOPIC_SAMPLE, len(df)), random_state=SEED)
topic_txts = topic_df['clean_bert'].tolist()

print(f"Encoding {len(topic_txts):,} texts with SBERT...")
topic_embs = sbert.encode(
    topic_txts, batch_size=256,
    show_progress_bar=True,
    normalize_embeddings=True
).astype('float32')
print(f"Embeddings shape: {topic_embs.shape}")
""")

code("""\
umap_model   = UMAP(n_neighbors=15, n_components=5, min_dist=0.0, metric='cosine', random_state=SEED)
hdbscan_model = HDBSCAN(min_cluster_size=100, metric='euclidean', prediction_data=True)

topic_model = BERTopic(
    umap_model=umap_model,
    hdbscan_model=hdbscan_model,
    nr_topics='auto',
    min_topic_size=100,
    verbose=True
)

topics, probs = topic_model.fit_transform(topic_txts, topic_embs)
topic_df = topic_df.copy()
topic_df['topic_id'] = topics

info = topic_model.get_topic_info()
print(f"\\nTopics found: {info.shape[0] - 1}  (plus outlier cluster -1)")
print(info.head(16).to_string())
""")

code("""\
info_filtered = info[info['Topic'] != -1].head(20)

plt.figure(figsize=(12, 8))
labels = [f"T{row['Topic']}: {row['Name'][:35]}" for _, row in info_filtered.iterrows()]
plt.barh(labels, info_filtered['Count'].values, color='teal')
plt.title("Top 20 BERTopic Topics by Volume", fontsize=14)
plt.xlabel("Complaint count")
plt.gca().invert_yaxis()
plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/topic_distribution.png", dpi=150)
plt.show()

topic_model.save(f"{OUTPUT_DIR}/bertopic_model")
print("BERTopic model saved.")
""")

# ─────────────────────────────────────────────────────────────
# SECTION 7: SBERT + FAISS
# ─────────────────────────────────────────────────────────────
md("---\n## 🔗 Section 7 — Semantic Similarity Search (SBERT + FAISS)")

code("""\
# Build knowledge base from CLOSED / RESOLVED tickets with resolution notes
kb_df = df[df['status'].isin(['Closed', 'Resolved'])].dropna(subset=['resolution_notes']).copy()
kb_df = kb_df.reset_index(drop=True)
print(f"Knowledge base size: {len(kb_df):,} resolved tickets")

# Compute or load embeddings
if os.path.exists(EMBEDDINGS_CACHE):
    print("Loading cached SBERT embeddings...")
    kb_embs = np.load(EMBEDDINGS_CACHE)
else:
    print(f"Encoding {len(kb_df):,} texts with SBERT...")
    kb_embs = sbert.encode(
        kb_df['clean_bert'].tolist(),
        batch_size=256,
        show_progress_bar=True,
        normalize_embeddings=True
    ).astype('float32')
    np.save(EMBEDDINGS_CACHE, kb_embs)
    print(f"Embeddings cached → {EMBEDDINGS_CACHE}")

print(f"Embeddings shape: {kb_embs.shape}")
""")

code("""\
# Build FAISS index (Inner Product ≡ Cosine Similarity on L2-normalised vectors)
dim         = kb_embs.shape[1]
faiss_index = faiss.IndexFlatIP(dim)
faiss_index.add(kb_embs)
print(f"FAISS index built with {faiss_index.ntotal:,} vectors")

faiss.write_index(faiss_index, FAISS_INDEX_PATH)
kb_df.to_csv(f"{OUTPUT_DIR}/resolved_kb.csv", index=False)
print("FAISS index & KB saved.")


def find_similar(query: str, top_k: int = 5) -> pd.DataFrame:
    \"\"\"Return top-k most similar resolved complaints for a query.\"\"\"
    q_emb = sbert.encode([clean_bert(query)], normalize_embeddings=True).astype('float32')
    scores, idxs = faiss_index.search(q_emb, top_k)
    result = kb_df.iloc[idxs[0]].copy()
    result['similarity_score'] = scores[0]
    return result[['issue_description', 'category', 'resolution_notes',
                   'assigned_team', 'similarity_score']]


# Demo
q = "I was charged twice for my subscription renewal this month"
print(f"Query: '{q}'\\n")
print(find_similar(q, top_k=3)[['category', 'similarity_score', 'resolution_notes']].to_string())
""")

# ─────────────────────────────────────────────────────────────
# SECTION 8: RESOLUTION TIME PREDICTION
# ─────────────────────────────────────────────────────────────
md("---\n## ⏱️ Section 8 — Resolution Time Prediction (XGBoost)")

code("""\
res_df = df.dropna(subset=['resolution_time_hours']).copy()
print(f"Rows with resolution time: {len(res_df):,}")
print(res_df['resolution_time_hours'].describe().round(2))

CAT_FEATS = ['product', 'channel', 'region', 'priority',
             'subscription_type', 'customer_segment', 'assigned_team']
NUM_FEATS = ['customer_age', 'customer_tenure_months',
             'previous_tickets', 'issue_complexity_score', 'reopen_count']

# Encode categoricals
le_encoders = {}
for col in CAT_FEATS:
    enc = LabelEncoder()
    res_df[col + '_enc'] = enc.fit_transform(res_df[col].astype(str))
    le_encoders[col] = enc

ENC_COLS = [c + '_enc' for c in CAT_FEATS]
""")

code("""\
# Compute SBERT embeddings for the resolution subset
print(f"Encoding {len(res_df):,} rows with SBERT for XGBoost features...")
res_embs = sbert.encode(
    res_df['clean_bert'].tolist(),
    batch_size=256,
    show_progress_bar=True,
    normalize_embeddings=True
).astype('float32')

X_meta = res_df[ENC_COLS + NUM_FEATS].values
X      = np.hstack([X_meta, res_embs])
y      = res_df['resolution_time_hours'].clip(upper=200).values

X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=SEED)
print(f"Feature matrix: {X.shape}")
""")

code("""\
xgb_device = 'cuda' if torch.cuda.is_available() else 'cpu'

xgb_reg = xgb.XGBRegressor(
    n_estimators=300,
    max_depth=6,
    learning_rate=0.1,
    subsample=0.8,
    colsample_bytree=0.8,
    device=xgb_device,
    random_state=SEED,
    verbosity=0
)
xgb_reg.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=50)

y_pred = xgb_reg.predict(X_te)
rmse   = np.sqrt(mean_squared_error(y_te, y_pred))
mae    = np.mean(np.abs(y_te - y_pred))
print(f"\\nResolution Time — RMSE: {rmse:.2f} hrs  |  MAE: {mae:.2f} hrs")

xgb_reg.save_model(f"{OUTPUT_DIR}/xgb_resolution_time.json")
print("XGBoost regressor saved.")
""")

code("""\
# SLA Breach binary classifier
print("Training SLA breach classifier...")
sla_df = df.copy()
sla_df['sla_label'] = (sla_df['sla_breached'] == 'Yes').astype(int)
for col in CAT_FEATS:
    enc = LabelEncoder()
    sla_df[col + '_enc'] = enc.fit_transform(sla_df[col].astype(str))

X_sla = sla_df[ENC_COLS + NUM_FEATS].values
y_sla = sla_df['sla_label'].values

Xs_tr, Xs_te, ys_tr, ys_te = train_test_split(X_sla, y_sla, test_size=0.2, random_state=SEED)

xgb_sla = xgb.XGBClassifier(
    n_estimators=200, max_depth=5, learning_rate=0.1,
    device=xgb_device, random_state=SEED, verbosity=0
)
xgb_sla.fit(Xs_tr, ys_tr, eval_set=[(Xs_te, ys_te)], verbose=50)

sla_pred = xgb_sla.predict(Xs_te)
print(f"SLA Breach → Accuracy: {accuracy_score(ys_te, sla_pred):.4f}  F1: {f1_score(ys_te, sla_pred):.4f}")
xgb_sla.save_model(f"{OUTPUT_DIR}/xgb_sla_breach.json")
print("SLA breach classifier saved.")
""")

# ─────────────────────────────────────────────────────────────
# SECTION 9: TEAM RECOMMENDATION
# ─────────────────────────────────────────────────────────────
md("---\n## 👥 Section 9 — Team Recommendation")

code("""\
CATEGORY_TO_TEAM = {
    'Billing'                : 'Billing',
    'Refunds'                : 'Billing',
    'Subscription'           : 'Billing',
    'Security'               : 'Security',
    'Authentication & Access': 'Security',
    'Bugs & Errors'          : 'Engineering',
    'Performance'            : 'Engineering',
    'Data Sync'              : 'Engineering',
    'Integrations & API'     : 'Engineering',
    'Account Management'     : 'Tier 1 Support',
    'Onboarding & How-To'    : 'Tier 1 Support',
}


def recommend_team_rule(category: str) -> str:
    return CATEGORY_TO_TEAM.get(category, 'Tier 1 Support')


def recommend_team_similarity(query: str, top_k: int = 10) -> tuple:
    similar = find_similar(query, top_k=top_k)
    votes   = similar['assigned_team'].value_counts()
    return votes.index[0], round(votes.iloc[0] / top_k, 2)


def recommend_team(query: str, predicted_category: str) -> dict:
    rule_team         = recommend_team_rule(predicted_category)
    sim_team, sim_conf = recommend_team_similarity(query)
    if rule_team == sim_team:
        return {'team': rule_team, 'confidence': 'High',   'method': 'Rule + Similarity'}
    return     {'team': rule_team, 'confidence': 'Medium', 'method': 'Rule-based'}


# Evaluate rule-based on a test sample
sample = test_df.sample(500, random_state=SEED)
rule_preds = sample['category'].apply(recommend_team_rule)
print("Rule-based Team Recommendation on 500 test samples:")
print(f"  Accuracy : {accuracy_score(sample['assigned_team'], rule_preds):.4f}")
print(f"  Macro F1 : {f1_score(sample['assigned_team'], rule_preds, average='macro'):.4f}")
print(classification_report(sample['assigned_team'], rule_preds))
""")

# ─────────────────────────────────────────────────────────────
# SECTION 10: FUSION REPORT
# ─────────────────────────────────────────────────────────────
md("---\n## 🧠 Section 10 — Complaint Intelligence Fusion Report")

code("""\
# Load fine-tuned BERT for inference
print("Loading saved distilBERT classifier...")
bert_infer = hf_pipeline(
    "text-classification",
    model=BERT_SAVE,
    tokenizer=BERT_SAVE,
    device=0 if torch.cuda.is_available() else -1,
    top_k=3
)
print("Ready.")
""")

code("""\
def generate_report(complaint_text: str) -> dict:
    \"\"\"
    Combines all NLP/ML module outputs into a single
    Complaint Intelligence Report.
    \"\"\"
    cleaned = clean_bert(complaint_text)

    # 1. Category classification
    bert_out  = bert_infer(cleaned)[0]
    top       = bert_out[0]
    pred_cat  = le.classes_[int(top['label'].split('_')[-1])]
    cat_conf  = round(top['score'], 4)
    top3_cats = [
        {'category': le.classes_[int(r['label'].split('_')[-1])], 'score': round(r['score'], 4)}
        for r in bert_out
    ]

    # 2. NER
    entities = extract_entities(complaint_text)

    # 3. Semantic similarity
    similar_raw  = find_similar(complaint_text, top_k=3)
    similar_list = [
        {
            'description'   : row['issue_description'][:120] + '...',
            'category'      : row['category'],
            'resolution'    : str(row['resolution_notes'])[:200],
            'similarity'    : round(float(row['similarity_score']), 4)
        }
        for _, row in similar_raw.iterrows()
    ]

    # 4. Team recommendation
    team_rec = recommend_team(complaint_text, pred_cat)

    return {
        'complaint'           : complaint_text,
        'predicted_category'  : pred_cat,
        'category_confidence' : cat_conf,
        'top_3_categories'    : top3_cats,
        'extracted_entities'  : entities,
        'similar_cases'       : similar_list,
        'recommended_team'    : team_rec['team'],
        'team_confidence'     : team_rec['confidence'],
        'team_method'         : team_rec['method'],
    }
""")

code("""\
# Run on demo complaints
demo_complaints = [
    "I was charged twice for my subscription renewal this month. Please investigate.",
    "Two-factor authentication codes are not being delivered to my phone.",
    "The data is not syncing across my devices on the Mobile App. Urgent.",
    "Where can I find the API documentation for bulk export?",
    "A team member has more permissions than they should have on our account.",
    "The dashboard is loading very slowly since yesterday's update.",
]

reports = []
for complaint in demo_complaints:
    r = generate_report(complaint)
    reports.append(r)
    print("=" * 65)
    print(f"Complaint  : {complaint}")
    print(f"Category   : {r['predicted_category']}  (conf: {r['category_confidence']})")
    print(f"Team       : {r['recommended_team']}  ({r['team_confidence']})")
    print(f"Entities   : {r['extracted_entities']}")
    if r['similar_cases']:
        top = r['similar_cases'][0]
        print(f"Top match  : [{top['similarity']:.3f}] {top['description']}")
        print(f"Resolution : {top['resolution'][:100]}...")
    print()

with open(f"{OUTPUT_DIR}/sample_reports.json", 'w') as f:
    json.dump(reports, f, indent=2)
print(f"Reports saved → {OUTPUT_DIR}/sample_reports.json")
""")

# ─────────────────────────────────────────────────────────────
# SECTION 11: SUMMARY
# ─────────────────────────────────────────────────────────────
md("---\n## 📋 Section 11 — Final Summary")

code("""\
print("=" * 60)
print("  COMPLAINT RESOLUTION AGENT — TRAINING SUMMARY")
print("=" * 60)

print(f"\\n{'Module':<40} {'Metric'}")
print("-" * 60)
print(f"{'TF-IDF + Logistic Regression':<40} Acc={baseline_acc:.4f}  F1={baseline_f1:.4f}")
print(f"{'distilBERT (fine-tuned)':<40} Acc={test_acc:.4f}  F1={bert_f1:.4f}")
print(f"{'Resolution Time (XGBoost)':<40} RMSE={rmse:.2f} hrs  MAE={mae:.2f} hrs")

print("\\n✅ Saved Artifacts:")
for item in sorted(os.listdir(OUTPUT_DIR)):
    full = os.path.join(OUTPUT_DIR, item)
    size = os.path.getsize(full) if os.path.isfile(full) else '(dir)'
    print(f"  {item:<50} {size}")

print("\\n🎉 All modules complete. Ready for AI Agent integration.")
""")

# ─────────────────────────────────────────────────────────────
# SECTION 12: ZIP ALL MODELS
# ─────────────────────────────────────────────────────────────
md("""---
## 📦 Section 12 — Download All Models (ZIP)
> Run this final cell after all training is done. It zips every saved artifact into a single file you can download from the **Output tab** on the right panel.
""")

code("""\
import zipfile, os, datetime

ZIP_PATH = f"{OUTPUT_DIR}/complaint_resolution_agent_models.zip"

# Folders and files to include
INCLUDE_DIRS  = ['bert_category_classifier', 'bertopic_model']
INCLUDE_FILES = [
    'tfidf_logreg.pkl',
    'faiss_index.bin',
    'resolved_kb.csv',
    'xgb_resolution_time.json',
    'xgb_sla_breach.json',
    'label_map.json',
    'sample_reports.json',
    'sbert_embeddings.npy',
    'eda_distributions.png',
    'text_length_dist.png',
    'baseline_confusion_matrix.png',
    'bert_training_curves.png',
    'bert_confusion_matrix.png',
    'topic_distribution.png',
    'xgb_feature_importance.png',
]

print(f"Creating zip archive...")
with zipfile.ZipFile(ZIP_PATH, 'w', zipfile.ZIP_DEFLATED) as zf:

    # Add individual files
    for fname in INCLUDE_FILES:
        fpath = os.path.join(OUTPUT_DIR, fname)
        if os.path.exists(fpath):
            zf.write(fpath, arcname=fname)
            print(f"  + {fname}")
        else:
            print(f"  - SKIP (not found): {fname}")

    # Add directories recursively
    for dname in INCLUDE_DIRS:
        dpath = os.path.join(OUTPUT_DIR, dname)
        if os.path.isdir(dpath):
            for root, dirs, files in os.walk(dpath):
                for file in files:
                    full_path  = os.path.join(root, file)
                    arc_name   = os.path.relpath(full_path, OUTPUT_DIR)
                    zf.write(full_path, arcname=arc_name)
                    print(f"  + {arc_name}")
        else:
            print(f"  - SKIP (dir not found): {dname}")

zip_size_mb = os.path.getsize(ZIP_PATH) / (1024 * 1024)
print(f"\\nZip created: {ZIP_PATH}")
print(f"Size       : {zip_size_mb:.1f} MB")
print("\\nDownload it from the OUTPUT tab on the right panel ->")
print("  complaint_resolution_agent_models.zip")
""")

# ─────────────────────────────────────────────────────────────
# WRITE NOTEBOOK
# ─────────────────────────────────────────────────────────────
nb.cells = cells

output_path = r"D:\Complaint Resolution Agent\Complaint_Resolution_Agent.ipynb"
with open(output_path, 'w', encoding='utf-8') as f:
    nbf.write(nb, f)

print(f"Notebook written -> {output_path}")
