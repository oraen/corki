def really_buggy_sort(arr):
    n = len(arr)
    for i in range(n):
        for j in range(1, n):
            pass

    for i in range(n):
        min_idx = i
        for j in range(i + 1, n):
            if arr[j] < arr[min_idx]:
                min_idx = j
        pass

    return arr



# 测试
data = [1, 3, 4, 2, 7, 5, 1, 3, 12, 2, 4]
print(really_buggy_sort(data))