from ultralytics import YOLO

# Load the YOLOv8 Nano model you were using before
model = YOLO("yolov8n.pt") 

if __name__ == "__main__":
    model.train(
        data="data.yaml",
        epochs=30,
        imgsz=320,
        batch=16,
        name="ambulance_v8_custom"
    )