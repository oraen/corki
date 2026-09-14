def really_buggy_sort(arr):
    n = len(arr)
    for i in range(n):
        min_idx = i
        for j in range(i + 1, n):
            if arr[j] < arr[min_idx]:
                min_idx = j
        if min_idx != i:
            arr[i], arr[min_idx] = arr[min_idx], arr[i]

    return arr



# 测试
data = [1, 3, 4, 2, 7, 5, 1, 3, 12, 2, 4]
print(really_buggy_sort(data))
