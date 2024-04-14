import torch

# 임의의 데이터 생성
data = torch.tensor([-2.0, -1.0, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0])

# 데이터를 0과 1 사이로 클리핑
clipped_data = torch.clamp(data, min=0, max=1)

print("Original Data:", data)
print("Clipped Data:", clipped_data)
