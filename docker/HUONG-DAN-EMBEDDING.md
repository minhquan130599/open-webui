# Bật model embedding (BAAI/bge-m3) cho phần Kiến thức

Khi bật embedding, tài liệu tải lên được chia đoạn, mỗi đoạn được tạo vector và lưu vào pgvector. Khi người
dùng hỏi, NCS AI tìm các đoạn gần nghĩa nhất với câu hỏi, kể cả khi câu hỏi không dùng đúng từ trong tài liệu.

## 1. Chọn nơi chạy model embedding

NCS AI gọi model embedding qua API dạng OpenAI (`/v1/embeddings`). Vì vậy model chạy ở đâu cũng được, chỉ
cần đổi URL.

| Cách | URL điền vào NCS AI | Ghi chú |
|---|---|---|
| **Container trên cùng server** (đã có sẵn trong docker-compose, service `embedding`) | `http://embedding:80/v1` | Chạy CPU, khoảng 2,5–3 GB RAM, nhanh vừa đủ cho tài liệu văn phòng. Lần đầu tải model khoảng 2,3 GB |
| **Pod RunPod (GPU)** | `https://<pod-id>-8000.proxy.runpod.net/v1` | Nhanh nhất. Chạy trên pod: `vllm serve BAAI/bge-m3 --task embed --port 8000` (bản vLLM mới: `--runner pooling`). Cần tiến trình hoặc pod riêng, không chung với model chat |
| **Dịch vụ khác** (OpenAI, server nội bộ…) | URL của dịch vụ, kết thúc `/v1` | Đổi `RAG_EMBEDDING_MODEL` cho đúng tên model của dịch vụ |

Bật container embedding trên server:

```bash
cd ~/ncschatui/deploy
sudo docker compose --profile embedding up -d embedding
sudo docker compose logs -f embedding        # chờ dòng "Ready" (lần đầu tải model mất vài phút)
```

Lệnh `docker compose up -d` bình thường (không có `--profile embedding`) không khởi động service này.
Nếu đã bật một lần thì sau này phải luôn thêm `--profile embedding` khi chạy `up`, để Docker không coi
container embedding là thừa.

## 2. Cài mới: đặt sẵn trong `.env`

Các biến dưới đây **chỉ có tác dụng ở lần cài đầu**, khi database chưa có cấu hình kiến thức:

```env
BYPASS_EMBEDDING_AND_RETRIEVAL=false
RAG_EMBEDDING_ENGINE=openai
RAG_EMBEDDING_MODEL=BAAI/bge-m3
RAG_EMBEDDING_BASE_URL=http://embedding:80/v1     # hoặc URL pod RunPod
RAG_EMBEDDING_API_KEY=none                        # khóa API; TEI không cần thì điền bất kỳ
RAG_EMBEDDING_BATCH_SIZE=32
CHUNK_SIZE=1200
CHUNK_OVERLAP=150
RAG_TOP_K=8
ENABLE_RAG_HYBRID_SEARCH=false
```

Chưa có dịch vụ embedding thì đặt `BYPASS_EMBEDDING_AND_RETRIEVAL=true`. Nếu để `false` mà URL trống
hoặc sai, việc tải tài liệu sẽ báo lỗi.

## 3. Server đã chạy: cập nhật cấu hình

Server hiện tại đã lưu cấu hình kiến thức trong database (đang bỏ qua embedding), nên **sửa `.env` không
đổi được gì**. Làm theo một trong hai cách dưới đây.

### Cách A — Sửa trên giao diện (khuyên dùng)

1. Nếu dùng container trên server, bật nó trước (mục 1).
2. Vào **Cài đặt quản trị → Tài liệu**:
   - Tắt **Bỏ qua embedding và truy xuất**.
   - **Embedding Model Engine**: chọn `OpenAI`, URL `http://embedding:80/v1` (hoặc URL pod), Khóa API, Model
     `BAAI/bge-m3`. Batch size đặt 32.
   - Chia đoạn 1200, chồng lấn 150, Top K 8.
   - Bấm **Lưu**. Có hiệu lực ngay, không cần khởi động lại.
3. Bấm **Lập chỉ mục lại vector kiến thức và bộ nhớ**, cũng ở trang này (xem mục 4).

### Cách B — Lấy lại giá trị từ `.env`

Cách này dùng khi muốn mọi server lấy cùng một cấu hình từ `.env`.

1. Sửa `.env` trên server như mục 2.
2. Xóa các khóa cũ trong database. Khi khởi động, NCS AI sẽ tạo lại chúng từ `.env`:

   ```bash
   cd ~/ncschatui/deploy
   sudo docker compose exec postgres psql -U openwebui -d openwebui -c "
   DELETE FROM config WHERE key IN (
     'rag.bypass_embedding_and_retrieval', 'rag.embedding_engine', 'rag.embedding_model',
     'rag.openai.api_base_url', 'rag.openai.api_key', 'rag.embedding_batch_size',
     'rag.chunk_size', 'rag.chunk_overlap', 'rag.top_k', 'rag.enable_hybrid_search');"
   ```

3. Khởi động lại NCS AI để nạp cấu hình mới:

   ```bash
   sudo docker compose --profile embedding up -d          # bỏ --profile embedding nếu dùng pod
   sudo docker compose restart open-webui
   ```

4. Kiểm tra trong **Cài đặt quản trị → Tài liệu** thấy đúng model và URL, rồi lập chỉ mục lại (mục 4).

Câu lệnh trên chỉ xóa cấu hình kiến thức. Không đụng tới tài liệu, chat hay các cấu hình khác.

## 4. Lập chỉ mục lại tài liệu cũ (bắt buộc)

Tài liệu tải lên khi còn "bỏ qua embedding" **chưa có vector**. Không lập chỉ mục lại thì không tìm được
tài liệu cũ.

- Bấm **Cài đặt quản trị → Tài liệu → Lập chỉ mục lại vector kiến thức và bộ nhớ**.
- Chạy nền. Thời gian tùy số lượng tài liệu, có thể từ vài phút đến cả giờ với container CPU.
- Mỗi lần **đổi sang model embedding khác** đều phải lập chỉ mục lại, vì vector của các model khác nhau
  không so sánh được với nhau.

## 5. Kiểm tra

- Trang giám sát (`http://127.0.0.1:8090`) → tab **Kiến thức**: sơ đồ phải hiện các bước "Chia đoạn" và
  "Tạo embedding" ở trạng thái bật, model `BAAI/bge-m3`. Các file mới tải lên có cột **Embedding** (thời gian).
- Tab **Thành phần & tài nguyên** → **Đo ngay**: dòng `embedding` phải OK, kèm "vector 1024 chiều".
- Tải một tài liệu vào kho kiến thức, rồi hỏi bằng từ khác với từ trong tài liệu. AI vẫn phải tìm ra
  đoạn liên quan.

## 6. Đổi model hoặc đổi nơi chạy về sau

Chỉ cần sửa trên giao diện (mục 3, cách A), không cần build lại image:

- **Từ container sang pod** (hoặc ngược lại): đổi URL. Vẫn dùng bge-m3 thì không phải lập chỉ mục lại.
- **Sang model khác**: đổi tên model, rồi lập chỉ mục lại. Nếu là container trên server thì sửa
  `EMBEDDING_MODEL_ID` trong `.env` và chạy lại `docker compose --profile embedding up -d embedding`.

Cột vector trong pgvector có 1536 chiều. Model có ít chiều hơn (bge-m3 có 1024) được tự bù bằng số 0 nên
không ảnh hưởng kết quả. Model có **nhiều hơn 1536 chiều** thì phải đặt
`PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH` và tạo lại bảng vector.
