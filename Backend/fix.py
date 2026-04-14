import os

# Path to your dataset
base_path = r"datasets\ambulance_yolo"

for split in ['train', 'val']:
    label_dir = os.path.join(base_path, split, 'labels')
    if not os.path.exists(label_dir): continue
    
    print(f"Fixing labels in {split}...")
    for filename in os.listdir(label_dir):
        if filename.endswith(".txt"):
            file_path = os.path.join(label_dir, filename)
            
            with open(file_path, 'r') as f:
                lines = f.readlines()
            
            # Rewrite file forcing the first number (class) to 0
            with open(file_path, 'w') as f:
                for line in lines:
                    parts = line.split()
                    if len(parts) > 0:
                        parts[0] = '0' 
                        f.write(" ".join(parts) + "\n")

print("✅ All labels are now set to Class 0: ambulance")