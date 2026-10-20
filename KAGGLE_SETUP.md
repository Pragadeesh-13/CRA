# Kaggle Setup Guide — Complaint Resolution Agent

## Step-by-step instructions to run the master notebook on Kaggle

---

### Step 1 — Upload the Dataset

1. Go to [kaggle.com/datasets](https://www.kaggle.com/datasets)
2. Click **New Dataset**
3. Upload `complaints-deduplicated.csv`
4. Name it exactly: **`complaint-resolution-agent`**
5. Set visibility to **Private**
6. Click **Create**

---

### Step 2 — Create the Notebook

1. Go to [kaggle.com/code](https://www.kaggle.com/code)
2. Click **New Notebook**
3. Click **File → Import Notebook**
4. Upload `Complaint_Resolution_Agent.ipynb`

---

### Step 3 — Add the Dataset to the Notebook

1. In the notebook editor, click **Add Data** (right panel)
2. Search for your dataset: `complaint-resolution-agent`
3. Click **Add**
4. The CSV will be available at:
   ```
   /kaggle/input/complaint-resolution-agent/complaints-deduplicated.csv
   ```
   > This matches the `DATA_PATH` already set in the Config cell — no changes needed.

---

### Step 4 — Enable GPU + Internet

In the notebook settings (right panel):

| Setting | Value |
|---|---|
| **Accelerator** | GPU T4 x2 |
| **Internet** | On (required for downloading models) |

---

### Step 5 — Run All Cells

Click **Run All** or press `Shift+Enter` through each cell.

**Estimated total runtime on T4 GPU:**

| Section | Estimated Time |
|---|---|
| Install & Setup | ~2 min |
| Data Loading & EDA | ~1 min |
| Preprocessing | ~3 min |
| TF-IDF Baseline | ~2 min |
| distilBERT Fine-tuning (3 epochs) | ~35–50 min |
| NER Extraction (full dataset) | ~8–12 min |
| BERTopic (50K sample) | ~5–8 min |
| FAISS Index Build | ~3–5 min |
| XGBoost Training | ~5–8 min |
| Team Recommendation | ~1 min |
| Fusion Report Demo | ~1 min |
| **Total** | **~65–90 min** |

---

### Step 6 — Download Saved Artifacts

After training, all model artifacts are saved to `/kaggle/working/`:

| File | Description |
|---|---|
| `bert_category_classifier/` | Fine-tuned distilBERT (tokenizer + model) |
| `tfidf_logreg.pkl` | TF-IDF + Logistic Regression baseline |
| `bertopic_model/` | BERTopic topic model |
| `faiss_index.bin` | FAISS similarity index |
| `resolved_kb.csv` | Resolved ticket knowledge base |
| `xgb_resolution_time.json` | Resolution time predictor |
| `xgb_sla_breach.json` | SLA breach classifier |
| `label_map.json` | Category ID → name mapping |
| `sample_reports.json` | Example fusion reports |
| `*.png` | Training curves + confusion matrices |

Download them via the **Output** tab on the right panel.

---

### Troubleshooting

| Issue | Fix |
|---|---|
| `FileNotFoundError: DATA_PATH` | Check dataset was added and name matches exactly |
| `CUDA out of memory` | Reduce `BATCH_SIZE` from 32 to 16 in Config cell |
| `bertopic install error` | Re-run the install cell manually |
| BERTopic takes too long | Reduce `TOPIC_SAMPLE` from 50000 to 20000 |
| FAISS search slow | Reduce KB size by filtering `status == 'Closed'` only |
